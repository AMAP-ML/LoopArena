"""BeyondSWE adapter executing the pinned Harbor task verifier in its official image."""

from __future__ import annotations

import json
import math
import re
import shlex
import shutil
import time

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

from pathlib import Path
from typing import Any

from .base import (
    base_receipt,
    beyondswe_evaluator_network,
    remaining_timeout_sec,
    run_argv,
)

_COLLECTED_RE = re.compile(r"\bcollected\s+(\d+)\s+items?\b", re.IGNORECASE)
_SUMMARY_RE = re.compile(
    r"\b(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b",
    re.IGNORECASE,
)


def _pytest_counts(output: str, *, expected: int) -> tuple[int, int]:
    collected_matches = _COLLECTED_RE.findall(output)
    collection_counts = [int(value) for value in collected_matches]
    # A protected test may start a nested pytest session after the main
    # collection header. Prefer the frozen expected count when it appeared
    # anywhere; the last header may belong to that nested session.
    collected = (
        expected
        if expected in collection_counts
        else (collection_counts[-1] if collection_counts else 0)
    )
    # Restrict status parsing to pytest's delimited terminal summary. Generic
    # setup logs such as "corrupt patch at line 44" must not become 44 errors.
    summary_lines = [
        line
        for line in output.splitlines()
        if line.strip().startswith("=")
        and line.strip().endswith("=")
        and _SUMMARY_RE.search(line)
    ]
    latest: dict[str, int] = {}
    for count, label in _SUMMARY_RE.findall(summary_lines[-1] if summary_lines else ""):
        latest[label.lower()] = int(count)
    executed = sum(
        latest.get(label, 0)
        for label in ("passed", "failed", "error", "errors", "xpassed")
    )
    return collected, executed


def _docker(
    executable: str,
    args: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
) -> tuple[int, str, str, float, bool]:
    return run_argv([executable, *args], cwd=cwd, timeout_sec=timeout_sec)


def _failure(
    plan: dict[str, Any],
    *,
    status: str,
    expected: int,
    wall_time_sec: float = 0.0,
    return_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    return base_receipt(
        plan,
        status=status,
        infrastructure_failure=True,
        task_passed=None,
        tests_expected=expected,
        wall_time_sec=wall_time_sec,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
    )


def evaluate_beyondswe_harbor(
    workspace: Path,
    output_dir: Path,
    plan: dict[str, Any],
    *,
    solve_sandbox: Any,
) -> dict[str, Any]:
    config = plan["adapter_config"]
    task_dir = Path(str(config.get("task_dir") or "")).resolve()
    tests_dir = task_dir / "tests"
    runtime = plan.get("runtime_identity") or {}
    docker = str(config.get("docker_executable") or "docker")
    task_config = task_dir / "task.toml"
    instance_config = tests_dir / "instance.json"
    try:
        task_settings = tomllib.loads(task_config.read_text(encoding="utf-8"))
        official_verifier = task_settings.get("verifier") or {}
        official_environment = task_settings.get("environment") or {}
        if not isinstance(official_verifier, dict) or not isinstance(
            official_environment, dict
        ):
            raise ValueError("task environment and verifier settings must be objects")
        raw_verifier_timeout = official_verifier.get("timeout_sec")
        if (
            isinstance(raw_verifier_timeout, bool)
            or not isinstance(raw_verifier_timeout, (int, float))
            or raw_verifier_timeout <= 0
        ):
            raise ValueError("verifier timeout must be positive")
        official_verifier_timeout = math.ceil(raw_verifier_timeout)
        raw_verifier_user = official_verifier.get("user")
        official_verifier_user = (
            str(raw_verifier_user).strip() if raw_verifier_user is not None else None
        )
        if not official_verifier_user:
            official_verifier_user = None
        verifier_environment_supported = (
            official_verifier.get("environment") is None
            and str(official_verifier.get("environment_mode") or "shared")
            .strip()
            .lower()
            != "separate"
        )
        official_network_policy, official_network = beyondswe_evaluator_network(
            task_settings,
            task_dir=task_dir,
        )
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError):
        task_settings = None
        official_verifier_timeout = 0
        official_verifier_user = None
        verifier_environment_supported = False
        official_environment = {}
        official_network_policy = ""
        official_network = ""
    try:
        official_instance = json.loads(instance_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        official_instance = None
    task_type = (
        str(official_instance.get("task") or "").strip().lower()
        if isinstance(official_instance, dict)
        else ""
    )
    workdir = str(
        runtime.get("workdir")
        or config.get("workdir")
        or (
            official_instance.get("workdir")
            if isinstance(official_instance, dict)
            else ""
        )
        or ""
    )
    if task_type == "doc2repo":
        evaluation_kind = "doc2repo_fractional"
        expected = (
            int(official_instance.get("test_suite_num") or 0)
            if isinstance(official_instance, dict)
            else 0
        )
        collection_config_valid = (
            tests_dir / "test_suite.zip"
        ).is_file() and expected > 0
    else:
        evaluation_kind = "f2p_p2p_binary"
        try:
            test_config = json.loads(
                (tests_dir / "test_config.json").read_text(encoding="utf-8")
            )
            official_unit_ids = [
                *list(test_config.get("fail_to_pass") or []),
                *list(test_config.get("pass_to_pass") or []),
            ]
        except (OSError, AttributeError, TypeError, json.JSONDecodeError):
            official_unit_ids = []
        expected = len(official_unit_ids)
        collection_config_valid = bool(official_unit_ids)
    if (
        not task_dir.is_dir()
        or not task_config.is_file()
        or not isinstance(task_settings, dict)
        or official_verifier_timeout <= 0
        or not verifier_environment_supported
        or not isinstance(official_instance, dict)
        or not (tests_dir / "test.sh").is_file()
        or not workdir.startswith("/")
        or not collection_config_valid
    ):
        return _failure(plan, status="setup_failed", expected=expected)

    timeout = int(plan.get("timeout_sec") or official_verifier_timeout + 300)
    deadline = time.monotonic() + timeout

    def invoke(
        args: list[str], *, timeout_cap: int | None = None
    ) -> tuple[int, str, str, float, bool]:
        remaining = remaining_timeout_sec(deadline)
        if remaining <= 0:
            return 124, "", "evaluator deadline exhausted", 0.0, True
        if timeout_cap is not None:
            if remaining < timeout_cap:
                return (
                    124,
                    "",
                    "evaluator setup consumed the verifier timeout allowance",
                    0.0,
                    True,
                )
            remaining = timeout_cap
        return _docker(docker, args, cwd=task_dir, timeout_sec=remaining)

    expected_image_id = str(runtime.get("image_id") or "").strip()
    cid = str(getattr(solve_sandbox, "container_id", "") or "")
    combined_stdout = ""
    combined_stderr = ""
    elapsed_total = 0.0
    image_identity = getattr(solve_sandbox, "image_identity", {}) or {}
    image_id = str(image_identity.get("image_id") or "")
    image_platform = str(runtime.get("platform") or "")
    if (
        not cid
        or image_id != expected_image_id
        or image_platform != "linux/amd64"
        or str(getattr(solve_sandbox, "mount_point", "")) != workdir
        or str(getattr(solve_sandbox, "network", "")) != official_network
    ):
        return _failure(
            plan,
            status="runtime_start_failed",
            expected=expected,
            stderr="live solve container does not match the Harbor task runtime",
        )

    def completed_task_failure(
        *,
        reason: str,
        return_code: int | None,
    ) -> dict[str, Any]:
        collected, executed = _pytest_counts(
            combined_stdout + "\n" + combined_stderr,
            expected=expected,
        )
        return base_receipt(
            plan,
            status="completed",
            infrastructure_failure=False,
            task_passed=False,
            tests_expected=expected,
            tests_collected=collected,
            tests_executed=executed,
            wall_time_sec=elapsed_total,
            return_code=return_code,
            stdout=combined_stdout,
            stderr=combined_stderr,
            details={
                "instance_id": official_instance.get("instance_id"),
                "task_type": task_type,
                "evaluation_kind": evaluation_kind,
                "failure_reason": reason,
                "official_image_id": image_id,
                "official_image_platform": image_platform,
                "network": official_network,
                "official_network_policy": official_network_policy,
                "evaluation_mode": "shared_solve_container",
                "workspace_payload_relative_path": ".",
            },
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    verifier_rc: int | None = None
    verifier_elapsed_sec: float | None = None
    try:
        verifier_user_args = (
            ["--user", official_verifier_user]
            if official_verifier_user is not None
            else []
        )
        rc, stdout, stderr, elapsed, timed_out = invoke(
            [
                "exec",
                "--user",
                "root",
                cid,
                "bash",
                "-lc",
                "rm -rf /tests /logs/verifier && mkdir -p /tests /logs/verifier",
            ],
        )
        elapsed_total += elapsed
        combined_stdout += stdout
        combined_stderr += stderr
        if timed_out or rc != 0:
            return _failure(
                plan,
                status="setup_failed",
                expected=expected,
                wall_time_sec=elapsed_total,
                return_code=rc,
                stdout=combined_stdout,
                stderr=combined_stderr,
            )

        stages = [
            (["cp", f"{tests_dir}/.", f"{cid}:/tests"], "setup_failed"),
            (
                [
                    "exec",
                    *verifier_user_args,
                    "--workdir",
                    workdir,
                    cid,
                    "timeout",
                    "--signal=TERM",
                    "--kill-after=10s",
                    str(official_verifier_timeout),
                    "bash",
                    "/tests/test.sh",
                ],
                "completed",
            ),
            (["cp", f"{cid}:/logs/verifier", str(output_dir)], "report_missing"),
        ]
        for args, failure_status in stages:
            rc, stdout, stderr, elapsed, timed_out = invoke(
                args,
                timeout_cap=(
                    official_verifier_timeout + 20 if args[0] == "exec" else None
                ),
            )
            elapsed_total += elapsed
            combined_stdout += stdout
            combined_stderr += stderr
            if args[0] == "exec":
                verifier_rc = rc
                verifier_elapsed_sec = elapsed
            if timed_out:
                return _failure(
                    plan,
                    status="execution_timeout",
                    expected=expected,
                    wall_time_sec=elapsed_total,
                    return_code=rc,
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                )
            # A task failure may make test.sh nonzero; the reward remains authoritative.
            if rc != 0 and args[0] != "exec":
                return _failure(
                    plan,
                    status=failure_status,
                    expected=expected,
                    wall_time_sec=elapsed_total,
                    return_code=rc,
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                )
    finally:
        if cid:
            # Harbor's verifier mutates the bind-mounted worktree while model
            # access is closed. Clear verifier-owned files as container root
            # first because Linux bind mounts may otherwise leave root-owned
            # files that the host user cannot remove. Regardless of that
            # outcome, attempt to restore the host worktree from the sealed
            # candidate snapshot so no private-test derivative survives.
            cleanup_errors: list[str] = []
            quoted_workdir = shlex.quote(workdir)
            restore_rc, _, restore_stderr, _, restore_timed_out = _docker(
                docker,
                [
                    "exec",
                    "--user",
                    "root",
                    cid,
                    "bash",
                    "-lc",
                    (
                        "status=0; "
                        "rm -rf /tests /logs/verifier || status=$?; "
                        f"find {quoted_workdir} -mindepth 1 -maxdepth 1 "
                        "-exec rm -rf -- {} + || status=$?; "
                        "exit $status"
                    ),
                ],
                cwd=task_dir,
                timeout_sec=60,
            )
            if restore_timed_out or restore_rc != 0:
                cleanup_errors.append(
                    restore_stderr.strip() or "container cleanup failed"
                )
            solve_workspace = Path(getattr(solve_sandbox, "workdir")).resolve()
            try:
                for child in solve_workspace.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                shutil.copytree(
                    workspace,
                    solve_workspace,
                    symlinks=True,
                    dirs_exist_ok=True,
                )
            except OSError as exc:
                cleanup_errors.append(f"host workspace restore failed: {exc}")
            if cleanup_errors:
                raise RuntimeError(
                    "failed to restore shared verifier environment: "
                    + "; ".join(cleanup_errors)
                )

    (output_dir / "adapter_stdout.txt").write_text(combined_stdout, encoding="utf-8")
    (output_dir / "adapter_stderr.txt").write_text(combined_stderr, encoding="utf-8")
    # Once the official verifier has started, reaching its task-declared
    # timeout is a candidate outcome rather than an evaluator outage. GNU
    # timeout returns 124 at the limit and may return 137 after its grace
    # period. An earlier 137 (for example an OOM) is likewise a task failure.
    if verifier_rc in {124, 137}:
        return completed_task_failure(
            reason=(
                "verifier_timeout"
                if verifier_rc == 124
                or (
                    verifier_elapsed_sec is not None
                    and verifier_elapsed_sec >= official_verifier_timeout
                )
                else "verifier_killed"
            ),
            return_code=verifier_rc,
        )
    reward_path = output_dir / "verifier" / "reward.txt"
    if not reward_path.is_file():
        return _failure(
            plan,
            status="report_missing",
            expected=expected,
            wall_time_sec=elapsed_total,
            return_code=verifier_rc,
            stdout=combined_stdout,
            stderr=combined_stderr,
        )
    try:
        reward = float(reward_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return _failure(
            plan,
            status="report_invalid",
            expected=expected,
            wall_time_sec=elapsed_total,
            return_code=verifier_rc,
            stdout=combined_stdout,
            stderr=combined_stderr,
        )
    collected, executed = _pytest_counts(
        combined_stdout + "\n" + combined_stderr,
        expected=expected,
    )
    # Some source repositories suppress pytest's collection header. A clean
    # zero exit plus an exact all-passed terminal summary still proves that
    # every frozen unit ran; use that count for the receipt.
    if collected == 0 and verifier_rc == 0 and executed == expected:
        collected = executed
    reward_valid = math.isfinite(reward) and 0.0 <= reward <= 1.0
    if evaluation_kind == "f2p_p2p_binary":
        passed_result_valid = (
            reward == 1.0
            and verifier_rc == 0
            and collected == expected
            and 0 < executed <= collected
        )
        failed_result_valid = reward == 0.0 and verifier_rc not in (None, 0)
        result_valid = reward_valid and (passed_result_valid or failed_result_valid)
    else:
        # Doc2Repo has a fractional non-pytest receipt contract. Preserve the
        # source reward semantics without applying binary collection rules.
        result_valid = reward_valid and verifier_rc is not None
    if not result_valid:
        return _failure(
            plan,
            status="report_invalid",
            expected=expected,
            wall_time_sec=elapsed_total,
            return_code=verifier_rc,
            stdout=combined_stdout,
            stderr=combined_stderr,
        )
    task_passed = reward == 1.0
    return base_receipt(
        plan,
        status="completed",
        infrastructure_failure=False,
        task_passed=task_passed,
        tests_expected=expected,
        tests_collected=collected,
        tests_executed=executed,
        wall_time_sec=elapsed_total,
        return_code=verifier_rc,
        stdout=combined_stdout,
        stderr=combined_stderr,
        details={
            "instance_id": official_instance.get("instance_id"),
            "task_type": task_type,
            "evaluation_kind": evaluation_kind,
            "reward": reward,
            "official_image_id": image_id,
            "official_image_platform": image_platform,
            "network": official_network,
            "official_network_policy": official_network_policy,
            "evaluation_mode": "shared_solve_container",
            "workspace_payload_relative_path": ".",
        },
    )


__all__ = ["evaluate_beyondswe_harbor"]
