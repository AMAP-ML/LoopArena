"""LoopsBench evaluator that delegates scoring to the pinned source runner.

The harness seals the model-produced workspace and stops the solve
sandbox before evaluation. This adapter evaluates a private copy in the same
pinned LoopsBench image. The official run-tests.sh exit code decides task
pass/fail after the runner's frozen unit and test-count contract is verified.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from .base import base_receipt, run_argv


_COLLECTED_RE = re.compile(r"\bcollected\s+(\d+)\s+items?\b", re.IGNORECASE)
_SUMMARY_RE = re.compile(
    r"\b(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b",
    re.IGNORECASE,
)
_UNIT_MARKER_RE = re.compile(r"^\[test\]\s+Testing:\s*(\S+)\s*$", re.MULTILINE)


def task_tree_sha256(root: Path) -> str:
    """Recompute the frozen LoopsBench task-tree digest."""

    rows: list[str] = []
    for path in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append(f"L\0{relative}\0{os.readlink(path)}")
        elif path.is_dir():
            rows.append(f"D\0{relative}")
        elif path.is_file():
            file_hash = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    file_hash.update(chunk)
            rows.append(
                f"F\0{relative}\0{path.stat().st_size}\0{file_hash.hexdigest()}"
            )
        else:
            raise ValueError(f"unsupported LoopsBench task entry: {path}")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _docker(
    executable: str,
    args: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
) -> tuple[int, str, str, float, bool]:
    return run_argv([executable, *args], cwd=cwd, timeout_sec=timeout_sec)


def _unit_sessions(output: str) -> list[dict[str, Any]]:
    """Parse source-runner output for evidence without affecting the score."""

    markers = list(_UNIT_MARKER_RE.finditer(output))
    chunks: list[tuple[str, str]]
    if markers:
        chunks = []
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(output)
            chunks.append((marker.group(1), output[marker.end() : end]))
    else:
        chunks = [("__task__", output)]

    sessions: list[dict[str, Any]] = []
    for unit_id, chunk in chunks:
        collections = [int(value) for value in _COLLECTED_RE.findall(chunk)]
        summary_lines = [
            line
            for line in chunk.splitlines()
            if line.strip().startswith("=")
            and line.strip().endswith("=")
            and _SUMMARY_RE.search(line)
        ]
        counts: dict[str, int] = {}
        for count, label in _SUMMARY_RE.findall(
            summary_lines[-1] if summary_lines else ""
        ):
            counts[label.lower()] = int(count)
        if not collections and not counts:
            continue
        sessions.append(
            {
                "unit_id": unit_id,
                "collected": collections[-1] if collections else sum(counts.values()),
                "passed": counts.get("passed", 0),
                "failed": counts.get("failed", 0),
                "errors": counts.get("error", 0) + counts.get("errors", 0),
                "skipped": counts.get("skipped", 0),
                "xfailed": counts.get("xfailed", 0),
                "xpassed": counts.get("xpassed", 0),
            }
        )
    return sessions


def _pytest_wrapper_session(output: str) -> list[dict[str, Any]]:
    """Parse the outer pytest wrapper without counting captured inner output."""

    collections = [int(value) for value in _COLLECTED_RE.findall(output)]
    summary_lines = [
        line
        for line in output.splitlines()
        if line.strip().startswith("=")
        and line.strip().endswith("=")
        and _SUMMARY_RE.search(line)
    ]
    if not collections or not summary_lines:
        return []
    counts: dict[str, int] = {}
    for count, label in _SUMMARY_RE.findall(summary_lines[-1]):
        counts[label.lower()] = int(count)
    return [
        {
            "unit_id": "__task__",
            # The wrapper collection is printed before the wrapped source
            # runner starts. Captured source output may contain later pytest
            # collection lines, so the first collection is the outer one.
            "collected": collections[0],
            "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0),
            "errors": counts.get("error", 0) + counts.get("errors", 0),
            "skipped": counts.get("skipped", 0),
            "xfailed": counts.get("xfailed", 0),
            "xpassed": counts.get("xpassed", 0),
        }
    ]


def _failure(
    plan: dict[str, Any],
    *,
    status: str,
    expected: int,
    wall_time_sec: float = 0.0,
    return_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    details: dict[str, Any] | None = None,
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
        details=details,
        solve_sandbox_stopped_before_evaluator=True,
    )


def evaluate_loopsbench(
    workspace: Path,
    output_dir: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    config = plan["adapter_config"]
    runtime = plan.get("runtime_identity") or {}
    task_dir = Path(str(config.get("task_dir") or "")).resolve()
    runtime_assets = Path(str(config.get("runtime_assets_dir") or "")).resolve()
    docker = str(config.get("docker_executable") or "docker")
    image_id = str(runtime.get("image_id") or "")
    platform = str(runtime.get("platform") or "")
    workdir = str(runtime.get("workdir") or "/workspace")
    network = str(plan.get("network") or "")
    expected = int(config.get("expected_test_count") or 0)
    expected_units = [str(value) for value in config.get("expected_unit_ids") or []]
    result_protocol = str(config.get("result_protocol") or "source_run_tests")
    task_yaml = task_dir / "task.yaml"
    run_tests = task_dir / "run-tests.sh"
    tests_dir = task_dir / "tests"

    try:
        task = yaml.safe_load(task_yaml.read_text(encoding="utf-8"))
        source_timeout = int(float(task["max_test_timeout_sec"]))
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        return _failure(plan, status="setup_failed", expected=expected)

    if (
        not workspace.is_dir()
        or not run_tests.is_file()
        or not tests_dir.is_dir()
        or not (runtime_assets / "bin" / "uv").is_file()
        or not (runtime_assets / "wheelhouse").is_dir()
        or not image_id
        or platform != "linux/amd64"
        or workdir != "/workspace"
        or network not in {"none", "bridge", "host"}
        or expected <= 0
        or source_timeout <= 0
        or result_protocol
        not in {
            "task_runner_pytest",
            "source_run_tests",
            "source_run_tests_task_level",
        }
    ):
        return _failure(plan, status="setup_failed", expected=expected)
    try:
        observed_task_tree_sha256 = task_tree_sha256(task_dir)
    except (OSError, ValueError):
        return _failure(plan, status="setup_failed", expected=expected)
    if observed_task_tree_sha256 != runtime.get("task_tree_sha256"):
        return _failure(plan, status="setup_failed", expected=expected)

    timeout = int(plan.get("timeout_sec") or source_timeout + 300)
    command_timeout = (
        timeout if result_protocol == "task_runner_pytest" else source_timeout
    )
    evaluator_root = "/opt/looparena-loopsbench-evaluator"
    container_name = f"looparena-loops-eval-{uuid.uuid4().hex[:20]}"
    setup_failure_marker = f"__LOOPARENA_SETUP_FAILED_{container_name}__"
    verifier_rc_prefix = f"__LOOPARENA_VERIFIER_RC_{container_name}__="
    output_dir.mkdir(parents=True, exist_ok=False)

    stdout = ""
    stderr = ""
    elapsed = 0.0
    verifier_rc: int | None = None
    timed_out = False
    try:
        with tempfile.TemporaryDirectory(
            prefix="workspace-", dir=output_dir
        ) as temporary:
            evaluator_workspace = Path(temporary) / "repo"
            shutil.copytree(workspace, evaluator_workspace, symlinks=True)
            verifier_command = (
                f"cd {evaluator_root} && python3 -P -m pytest "
                f"{shlex.quote(evaluator_root + '/tests/test_task_runner.py')} -rA"
                if result_protocol == "task_runner_pytest"
                else f"bash {shlex.quote(evaluator_root + '/run-tests.sh')}"
            )
            shell_command = (
                "set +e; "
                f"rm -rf /tests; ln -s {evaluator_root}/tests /tests; "
                f"export PATH={evaluator_root}/runtime/bin:$PATH; "
                "export PIP_NO_INDEX=1; "
                f"export PIP_FIND_LINKS={evaluator_root}/runtime/wheelhouse; "
                "export UV_OFFLINE=1; "
                f"export UV_FIND_LINKS={evaluator_root}/runtime/wheelhouse; "
                f"export TEST_DIR={evaluator_root}/tests; "
                f"export UNIT_TEST_DIR={evaluator_root}/tests; "
                f"if [ ! -d {evaluator_root} ]; then "
                f"printf '%s\\n' {shlex.quote(setup_failure_marker)} >&2; "
                "chmod -R a+rwX /workspace 2>/dev/null || true; exit 125; fi; "
                f"(cd {evaluator_root} && "
                "python3 -P -c 'import pytest') >/dev/null 2>&1 || "
                "uv pip install --python \"$(command -v python3)\" "
                "--break-system-packages --no-index "
                f"--find-links {evaluator_root}/runtime/wheelhouse pytest==8.4.1; "
                "setup_rc=$?; "
                "if [ \"$setup_rc\" -ne 0 ]; then "
                f"printf '%s\\n' {shlex.quote(setup_failure_marker)} >&2; "
                "chmod -R a+rwX /workspace 2>/dev/null || true; exit 125; fi; "
                f"timeout --signal=TERM --kill-after=10s {command_timeout} "
                f"bash -lc {shlex.quote(verifier_command)}; "
                "test_rc=$?; "
                f"printf '{verifier_rc_prefix}%s\\n' \"$test_rc\" >&2; "
                "chmod -R a+rwX /workspace 2>/dev/null || true; "
                "exit \"$test_rc\""
            )
            argv = [
                "run",
                "--rm",
                "--name",
                container_name,
                "--platform",
                "linux/amd64",
                "--network",
                network,
                "--workdir",
                workdir,
                "--mount",
                (
                    "type=bind,src="
                    f"{evaluator_workspace},dst={workdir}"
                ),
                "--mount",
                (
                    "type=bind,src="
                    f"{tests_dir},dst={evaluator_root}/tests,readonly"
                ),
                "--mount",
                (
                    "type=bind,src="
                    f"{run_tests},dst={evaluator_root}/run-tests.sh,readonly"
                ),
                "--mount",
                (
                    "type=bind,src="
                    f"{runtime_assets},dst={evaluator_root}/runtime,readonly"
                ),
                "--entrypoint",
                "bash",
                image_id,
                "-lc",
                shell_command,
            ]
            verifier_rc, stdout, stderr, elapsed, timed_out = _docker(
                docker,
                argv,
                cwd=task_dir,
                timeout_sec=timeout,
            )
    except OSError as exc:
        return _failure(
            plan,
            status="setup_failed",
            expected=expected,
            wall_time_sec=elapsed,
            return_code=verifier_rc,
            stdout=stdout,
            stderr=f"{stderr}\n{type(exc).__name__}: {exc}".strip(),
        )
    finally:
        _docker(
            docker,
            ["rm", "-f", container_name],
            cwd=task_dir,
            timeout_sec=60,
        )

    if timed_out:
        return _failure(
            plan,
            status="execution_timeout",
            expected=expected,
            wall_time_sec=elapsed,
            return_code=verifier_rc,
            stdout=stdout,
            stderr=stderr,
        )
    combined_output = stdout + "\n" + stderr
    source_match = re.search(
        re.escape(verifier_rc_prefix) + r"(\d{1,3})\b", combined_output
    )
    source_rc = int(source_match.group(1)) if source_match else None
    if setup_failure_marker in combined_output:
        return _failure(
            plan,
            status="setup_failed",
            expected=expected,
            wall_time_sec=elapsed,
            return_code=verifier_rc,
            stdout=stdout,
            stderr=stderr,
        )
    if source_rc is None or source_rc != verifier_rc:
        return _failure(
            plan,
            status="execution_killed" if verifier_rc == 137 else "result_invalid",
            expected=expected,
            wall_time_sec=elapsed,
            return_code=verifier_rc,
            stdout=stdout,
            stderr=stderr,
        )
    if verifier_rc == 124 and result_protocol == "task_runner_pytest":
        return _failure(
            plan,
            status="execution_timeout",
            expected=expected,
            wall_time_sec=elapsed,
            return_code=verifier_rc,
            stdout=stdout,
            stderr=stderr,
        )

    if result_protocol == "task_runner_pytest":
        sessions = _pytest_wrapper_session(combined_output)
    elif result_protocol == "source_run_tests_task_level":
        # This protocol treats completion of the unmodified source runner as
        # one task-level observation. The runner's own exit code remains the
        # sole pass/fail decision; no replacement evaluator is introduced.
        sessions = [
            {
                "unit_id": "__task__",
                "collected": 1,
                "passed": int(verifier_rc == 0),
                "failed": int(verifier_rc != 0),
                "errors": 0,
                "skipped": 0,
                "xfailed": 0,
                "xpassed": 0,
            }
        ]
    else:
        sessions = _unit_sessions(combined_output)
    collected = sum(int(row["collected"]) for row in sessions)
    executed = sum(
        int(row[label])
        for row in sessions
        for label in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed")
    )
    observed_units = [str(row["unit_id"]) for row in sessions]
    if result_protocol == "task_runner_pytest":
        complete = collected == expected == 1 and executed == expected
    elif result_protocol == "source_run_tests_task_level":
        complete = collected == 1 and executed == 1
    elif verifier_rc == 124:
        complete = collected > 0
    elif verifier_rc == 0:
        complete = (
            collected == expected
            and executed == expected
            and (not expected_units or observed_units == expected_units)
        )
    else:
        # Source runners commonly stop after the first failing unit.  A fully
        # observed non-empty prefix is sufficient to establish task failure;
        # a pass still requires the complete frozen test set above.
        complete = (
            collected > 0
            and executed == collected
            and (
                not expected_units
                or observed_units == expected_units[: len(observed_units)]
            )
        )
    if not complete:
        return _failure(
            plan,
            status="result_invalid",
            expected=expected,
            wall_time_sec=elapsed,
            return_code=verifier_rc,
            stdout=stdout,
            stderr=stderr,
        )
    task_passed = verifier_rc == 0
    if task_passed:
        failure_reason = None
    else:
        failure_reason = (
            "verifier_timeout" if verifier_rc == 124 else "source_tests_failed"
        )

    return base_receipt(
        plan,
        status="completed",
        infrastructure_failure=False,
        task_passed=task_passed,
        tests_expected=expected or collected,
        tests_collected=collected,
        tests_executed=executed,
        wall_time_sec=elapsed,
        return_code=verifier_rc,
        stdout=stdout,
        stderr=stderr,
        details={
            "task_id": task_dir.name,
            "evaluation_kind": "official-source-runner-binary-exit-code",
            "failure_reason": failure_reason,
            "result_protocol": result_protocol,
            "unit_sessions": sessions,
            "expected_units": expected_units,
            "official_image_id": image_id,
            "official_image_platform": platform,
            "network": network,
            "evaluation_mode": "sealed-workspace-official-image-after-model-access",
            "gold_patches_exposed_to_model": False,
        },
        solve_sandbox_stopped_before_evaluator=True,
    )


__all__ = ["evaluate_loopsbench", "task_tree_sha256"]
