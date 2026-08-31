"""SCBench adapter using the pinned official ``eval-snapshot`` command."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from .base import (
    base_receipt,
    read_json,
    remaining_timeout_sec,
    run_argv,
    scbench_evaluator_network,
)

_OFFICIAL_EVALUATOR_DEPENDENCIES = (
    "pytest",
    "pytest-json-ctrf",
    "pytest-json-report",
    "pytest-timeout",
    "jsonschema",
    "deepdiff",
)


def _runtime_environment_config(
    environment: dict[str, Any],
    output_dir: Path,
    *,
    evaluation_image_reference: str,
) -> tuple[Path, dict[str, Any]]:
    """Add operational dependency caches without changing SCBench semantics."""

    runtime_environment = copy.deepcopy(environment)
    if not evaluation_image_reference.startswith("slop-code:"):
        raise ValueError("SCBench image reference must use the runner's tag prefix")
    runtime_environment["name"] = evaluation_image_reference.removeprefix("slop-code:")
    docker = runtime_environment.get("docker")
    environment_settings = runtime_environment.get("environment")
    if not isinstance(docker, dict) or not isinstance(environment_settings, dict):
        raise ValueError(
            "SCBench environment is missing Docker or environment settings"
        )
    mounts = docker.setdefault("extra_mounts", {})
    env = environment_settings.setdefault("env", {})
    if not isinstance(mounts, dict) or not isinstance(env, dict):
        raise ValueError("SCBench environment mounts and variables must be objects")

    details: dict[str, Any] = {
        "persistent_uv_cache": False,
        "dependency_install": "source_network_with_persistent_cache",
    }
    cache_value = os.environ.get("LOOPARENA_SCBENCH_UV_CACHE_DIR", "").strip()
    if cache_value:
        cache_dir = Path(cache_value).expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        mounts[str(cache_dir)] = {"bind": "/tmp/uv-cache", "mode": "rw"}
        details["persistent_uv_cache"] = True

    setup = runtime_environment.get("setup")
    commands = setup.get("eval_commands") if isinstance(setup, dict) else None
    if not isinstance(commands, list):
        raise ValueError("SCBench environment has no evaluator setup commands")
    if not any(
        str(command).strip() == "uv add -r requirements.txt" for command in commands
    ):
        raise ValueError("SCBench evaluator dependency command is not the pinned form")

    runtime_path = output_dir / "runtime_environment.yaml"
    runtime_path.write_text(
        yaml.safe_dump(runtime_environment, sort_keys=False),
        encoding="utf-8",
    )
    return runtime_path, details


def _evaluator_dependencies_available(
    *,
    private_bundle: Path,
    environment: dict[str, Any],
    evaluation_image_reference: str,
    docker_executable: str,
    timeout_sec: int,
) -> tuple[bool, dict[str, Any]]:
    """Probe evaluator-owned packages without executing candidate code."""

    try:
        problem = yaml.safe_load(
            (private_bundle / "config.yaml").read_text(encoding="utf-8")
        )
        problem_dependencies = (
            problem.get("test_dependencies") if isinstance(problem, dict) else []
        )
        if problem_dependencies is None:
            problem_dependencies = []
        if not isinstance(problem_dependencies, list) or not all(
            isinstance(value, str) and value.strip() for value in problem_dependencies
        ):
            raise ValueError("SCBench test_dependencies must be strings")
        dependencies = list(
            dict.fromkeys([*_OFFICIAL_EVALUATOR_DEPENDENCIES, *problem_dependencies])
        )
        network = scbench_evaluator_network(environment)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return False, {"error": f"{type(exc).__name__}: {exc}"}

    argv = [
        docker_executable,
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        network,
    ]
    cache_value = os.environ.get("LOOPARENA_SCBENCH_UV_CACHE_DIR", "").strip()
    if cache_value:
        cache_dir = Path(cache_value).expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        argv.extend(
            [
                "-e",
                "UV_CACHE_DIR=/tmp/uv-cache",
                "-v",
                f"{cache_dir}:/tmp/uv-cache",
            ]
        )
    argv.extend(
        [
            evaluation_image_reference,
            "uvx",
            *[f"--with={dependency}" for dependency in dependencies],
            "pytest",
            "--version",
        ]
    )
    rc, stdout, stderr, elapsed, timed_out = run_argv(
        argv,
        cwd=private_bundle,
        timeout_sec=max(1, timeout_sec),
    )
    return rc == 0 and not timed_out, {
        "return_code": rc,
        "timed_out": timed_out,
        "wall_time_sec": round(elapsed, 6),
        "network": network,
        "dependencies": dependencies,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
    }


def _evaluation_image_identity(
    plan: dict[str, Any],
    environment: dict[str, Any],
    docker_executable: str,
) -> tuple[str, str, str] | None:
    """Return the pinned image selected by the SCBench environment."""

    runtime = plan.get("runtime_identity") or {}
    reference = runtime.get("evaluation_image_reference")
    expected_id = runtime.get("evaluation_image_id")
    expected_platform = runtime.get("evaluation_image_platform") or runtime.get(
        "platform"
    )
    if not all(isinstance(value, str) and value for value in (reference, expected_id)):
        return None
    if not reference.startswith("slop-code:"):
        return None
    proc = subprocess.run(
        [docker_executable, "image", "inspect", reference],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        inspected = json.loads(proc.stdout)
        image = inspected[0]
        actual_id = image["Id"]
        actual_platform = f"{image['Os']}/{image['Architecture']}"
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if actual_id != expected_id:
        return None
    if (
        isinstance(expected_platform, str)
        and expected_platform
        and actual_platform != expected_platform
    ):
        return None
    return expected_id, actual_platform, actual_id


def _flatten_test_ids(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            found.extend(_flatten_test_ids(item))
    elif isinstance(value, dict):
        for key in sorted(value):
            found.extend(_flatten_test_ids(value[key]))
    return found


def _result_test_ids(value: object) -> list[str]:
    """Bind result occurrences to groups so duplicate raw IDs stay distinct."""

    if not isinstance(value, dict):
        return []
    found: list[str] = []
    for raw_group in sorted(value):
        group = str(raw_group)
        raw_ids = sorted(_flatten_test_ids(value[raw_group]))
        totals: dict[str, int] = {}
        for raw_id in raw_ids:
            totals[raw_id] = totals.get(raw_id, 0) + 1
        occurrences: dict[str, int] = {}
        for raw_id in raw_ids:
            occurrences[raw_id] = occurrences.get(raw_id, 0) + 1
            suffix = f"#{occurrences[raw_id]}" if totals[raw_id] > 1 else ""
            found.append(f"{group}::{raw_id}{suffix}")
    return found


def _official_result_count_evidence(
    report: dict[str, Any], *, expected: int
) -> tuple[int, int, bool]:
    """Return the official-result evidence used for a complete evaluation.

    The pinned SCBench runner's ``pytest_collected`` is derived from an
    auxiliary ``--collect-only`` listing.  Its parser intentionally discards
    node IDs containing spaces, so it can undercount parameterized tests even
    when the official execution and score are complete.  The authoritative
    evidence is the emitted result occurrences and the official category
    totals, both of which must match the frozen expected denominator.
    """

    result_occurrences = len(_result_test_ids(report.get("tests")))
    total_counts = report.get("total_counts")
    categorized_total = (
        sum(
            value
            for value in total_counts.values()
            if isinstance(value, int) and not isinstance(value, bool)
        )
        if isinstance(total_counts, dict)
        else 0
    )
    return (
        result_occurrences,
        categorized_total,
        result_occurrences == expected and categorized_total == expected,
    )


def _group_count(mapping: object, name: str) -> int:
    if not isinstance(mapping, dict):
        return 0
    for key, value in mapping.items():
        if str(key).lower() == name.lower() and isinstance(value, int):
            return value
    return 0


def _passes_policy(report: dict[str, Any], policy: str) -> bool:
    totals = report.get("total_counts")
    passed = report.get("pass_counts")
    if policy == "core-cases":
        total = _group_count(totals, "core")
        return total > 0 and _group_count(passed, "core") == total
    if policy in {"all-non-error-cases", "all-cases"}:
        scored_total = 0
        groups = ["core", "functionality", "regression"]
        if policy == "all-cases":
            groups.append("error")
        for group in groups:
            total = _group_count(totals, group)
            scored_total += total
            if total:
                if _group_count(passed, group) != total:
                    return False
        return scored_total > 0
    raise ValueError(f"unsupported SCBench pass policy: {policy}")


def evaluate_scbench(
    workspace: Path,
    output_dir: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    config = plan["adapter_config"]
    runner_root = Path(str(config.get("runner_root") or "")).resolve()
    env_config = Path(str(config.get("env_config") or "")).resolve()
    private_bundle = Path(str(config.get("private_bundle_path") or "")).resolve()
    problems_root = Path(
        str(config.get("problems_root") or private_bundle.parent)
    ).resolve()
    problem_name = str(config.get("problem_name") or private_bundle.name)
    checkpoint = str(config.get("checkpoint") or "")
    docker_executable = str(config.get("docker_executable") or "docker")
    raw_expected = config.get("tests_expected")
    expected = (
        raw_expected
        if isinstance(raw_expected, int)
        and not isinstance(raw_expected, bool)
        and raw_expected > 0
        else 0
    )
    try:
        environment = yaml.safe_load(env_config.read_text(encoding="utf-8"))
        scbench_evaluator_network(environment)
    except (OSError, ValueError, yaml.YAMLError):
        environment = None
    if (
        not runner_root.is_dir()
        or not problems_root.is_dir()
        or not private_bundle.is_dir()
        or not env_config.is_file()
        or not isinstance(environment, dict)
        or not problem_name
        or not checkpoint
        or expected <= 0
    ):
        return base_receipt(
            plan,
            status="setup_failed",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
        )
    evaluation_image_identity = _evaluation_image_identity(
        plan,
        environment,
        docker_executable,
    )
    if evaluation_image_identity is None:
        return base_receipt(
            plan,
            status="setup_failed",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
        )
    timeout = int(plan.get("timeout_sec") or 3600)
    deadline = time.monotonic() + timeout
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        runtime_env_config, dependency_runtime = _runtime_environment_config(
            environment,
            output_dir,
            evaluation_image_reference=str(
                (plan.get("runtime_identity") or {})["evaluation_image_reference"]
            ),
        )
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return base_receipt(
            plan,
            status="setup_failed",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
            stderr=f"{type(exc).__name__}: {exc}",
        )
    argv = [
        str(config.get("uv_executable") or "uv"),
        "run",
        "--project",
        str(runner_root),
        "slop-code",
        "--quiet",
        "eval-snapshot",
        str(workspace),
        "-o",
        str(output_dir),
        "-p",
        problem_name,
        "-c",
        checkpoint,
        "-e",
        str(runtime_env_config),
    ]
    evaluation_timeout = remaining_timeout_sec(deadline)
    if evaluation_timeout <= 0:
        return base_receipt(
            plan,
            status="execution_timeout",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
        )
    rc, stdout, stderr, elapsed, timed_out = run_argv(
        argv,
        cwd=runner_root,
        timeout_sec=evaluation_timeout,
        env={"SCBENCH_PROBLEMS_PATH": str(problems_root)},
    )
    elapsed_total = elapsed
    (output_dir / "adapter_stdout.txt").write_text(stdout, encoding="utf-8")
    (output_dir / "adapter_stderr.txt").write_text(stderr, encoding="utf-8")
    if timed_out:
        return base_receipt(
            plan,
            status="execution_timeout",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
            wall_time_sec=elapsed_total,
            return_code=rc,
            stdout=stdout,
            stderr=stderr,
        )
    report_path = output_dir / "evaluation.json"
    if not report_path.is_file():
        return base_receipt(
            plan,
            status="report_missing",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
            wall_time_sec=elapsed_total,
            return_code=rc,
            stdout=stdout,
            stderr=stderr,
        )
    try:
        report = read_json(report_path)
        collection_encoding = "checkpoint_group_bound"
        reported_collected = int(report.get("pytest_collected") or 0)
        listed_test_occurrences, categorized_total, result_counts_complete = (
            _official_result_count_evidence(report, expected=expected)
        )
        upstream_infrastructure_failure = bool(report.get("infrastructure_failure"))
        report_matches_target = report.get("problem_name") == problem_name and str(
            report.get("checkpoint_name") or ""
        ) in (checkpoint, f"checkpoint_{checkpoint}")
        pytest_exit = report.get("pytest_exit_code")
        if not report_matches_target:
            return base_receipt(
                plan,
                status="report_invalid",
                infrastructure_failure=True,
                task_passed=None,
                tests_expected=expected,
                tests_collected=listed_test_occurrences,
                tests_executed=categorized_total,
                wall_time_sec=elapsed_total,
                return_code=rc,
                stdout=stdout,
                stderr=stderr,
            )

        # Once the official evaluator has produced a structurally valid report
        # for the requested candidate workspace, syntax, import, collection,
        # no-tests, setup, and ordinary assertion failures are candidate
        # outcomes.  SCBench's upstream ``infrastructure_failure`` flag also
        # covers those candidate-controllable failures, so trusting it here
        # would let broken submissions leave the benchmark denominator.
        # Host-side setup failures, timeouts, and missing/invalid reports are
        # already handled above as infrastructure failures.
        status = "completed"
        infrastructure_failure = False
        if not result_counts_complete or pytest_exit not in (0, 1):
            if upstream_infrastructure_failure:
                probe_timeout = min(300, remaining_timeout_sec(deadline))
                dependencies_available, dependency_probe = (
                    _evaluator_dependencies_available(
                        private_bundle=private_bundle,
                        environment=environment,
                        evaluation_image_reference=str(
                            (plan.get("runtime_identity") or {})[
                                "evaluation_image_reference"
                            ]
                        ),
                        docker_executable=docker_executable,
                        timeout_sec=probe_timeout,
                    )
                )
                elapsed_total += float(dependency_probe.get("wall_time_sec") or 0.0)
                if not dependencies_available:
                    return base_receipt(
                        plan,
                        status="evaluator_dependencies_unavailable",
                        infrastructure_failure=True,
                        task_passed=None,
                        tests_expected=expected,
                        tests_collected=listed_test_occurrences,
                        tests_executed=categorized_total,
                        wall_time_sec=elapsed_total,
                        return_code=rc,
                        stdout=stdout,
                        stderr=stderr,
                        details={"dependency_probe": dependency_probe},
                    )
            task_passed = False
        else:
            # The frozen denominator is defined by executed official result
            # entries and official category totals.  A skipped or omitted
            # result therefore cannot satisfy the all-cases policy.
            policy = str(
                plan.get("pass_policy") or config.get("pass_policy") or "all-cases"
            )
            task_passed = (
                result_counts_complete
                and (policy != "all-cases" or categorized_total == expected)
            ) and _passes_policy(report, policy)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return base_receipt(
            plan,
            status="report_invalid",
            infrastructure_failure=True,
            task_passed=None,
            tests_expected=expected,
            wall_time_sec=elapsed_total,
            return_code=rc,
            stdout=stdout,
            stderr=stderr,
        )
    return base_receipt(
        plan,
        status=status,
        infrastructure_failure=infrastructure_failure,
        task_passed=task_passed,
        tests_expected=expected,
        tests_collected=listed_test_occurrences,
        tests_executed=categorized_total,
        wall_time_sec=elapsed_total,
        return_code=rc,
        stdout=stdout,
        stderr=stderr,
        details={
            "problem_name": problem_name,
            "checkpoint": checkpoint,
            "official_evaluation_image_id": evaluation_image_identity[0],
            "official_evaluation_image_platform": evaluation_image_identity[1],
            "observed_docker_image_id": evaluation_image_identity[2],
            "official_pytest_exit_code": report.get("pytest_exit_code"),
            "official_infrastructure_failure": upstream_infrastructure_failure,
            "official_reported_pytest_collected": reported_collected,
            "result_test_occurrences": listed_test_occurrences,
            "result_count_encoding": collection_encoding,
            "dependency_runtime": dependency_runtime,
        },
    )


__all__ = ["evaluate_scbench"]
