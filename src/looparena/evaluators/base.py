"""Shared boundary for benchmark-specific terminal evaluator adapters."""

from __future__ import annotations

import json
import math
import os
import platform
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from looparena.harness.evaluator_protocol import (
    evaluator_identity,
    validate_evaluation_receipt,
    validate_evaluator_plan,
)


def scbench_evaluator_network(environment: object) -> str:
    """Resolve the Docker network exactly as the official SCBench runtime does."""

    docker = environment.get("docker") if isinstance(environment, dict) else None
    if not isinstance(docker, dict):
        raise ValueError("SCBench environment must define docker settings")
    network = docker.get("network")
    effective = "bridge" if network is None else str(network).strip()
    if effective == "host" and platform.system() != "Linux":
        effective = "bridge"
    if effective not in {"none", "bridge", "host"}:
        raise ValueError(f"unsupported SCBench evaluator network: {effective}")
    return effective


def _beyondswe_docker_network(policy: object) -> tuple[str, str]:
    normalized = str(policy or "").strip().lower().replace("_", "-")
    if normalized == "public":
        return normalized, "bridge"
    if normalized in {"none", "no-network"}:
        return "no-network", "none"
    if normalized == "allowlist":
        raise ValueError("BeyondSWE allowlist networking is not yet supported")
    raise ValueError(f"unsupported BeyondSWE network policy: {policy}")


def _beyondswe_compose_network(task_dir: Path | None) -> str | None:
    """Return the main service's explicit task-authored Docker network."""

    if task_dir is None:
        return None
    compose_path = task_dir / "environment" / "docker-compose.yaml"
    if not compose_path.is_file():
        return None
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose.get("services") if isinstance(compose, dict) else None
    main = services.get("main") if isinstance(services, dict) else None
    if not isinstance(main, dict) or "network_mode" not in main:
        return None
    network = str(main.get("network_mode") or "").strip().lower()
    if network not in {"none", "bridge", "host"}:
        raise ValueError(f"unsupported BeyondSWE compose network: {network}")
    return network


def beyondswe_solve_network(
    task_settings: object,
    *,
    task_dir: Path | None = None,
) -> tuple[str, str]:
    """Resolve the Harbor task environment to its solve Docker network."""

    if not isinstance(task_settings, dict):
        raise ValueError("BeyondSWE task settings must be an object")
    environment = task_settings.get("environment") or {}
    if not isinstance(environment, dict):
        raise ValueError("BeyondSWE environment settings must be an object")
    if "network_mode" in environment:
        policy = environment.get("network_mode")
    elif "allow_internet" in environment:
        policy = "public" if environment.get("allow_internet") else "no-network"
    else:
        policy = "public"
    policy_name, network = _beyondswe_docker_network(policy)
    return policy_name, _beyondswe_compose_network(task_dir) or network


def beyondswe_evaluator_network(
    task_settings: object,
    *,
    task_dir: Path | None = None,
) -> tuple[str, str]:
    """Resolve Harbor verifier policy to an auditable Docker network mode.

    Harbor defaults the task environment to public networking. A verifier can
    override that policy directly or through a separate verifier environment.
    LoopArena currently supports Harbor's public and no-network policies; an
    allowlist needs Harbor's policy enforcement rather than a plain Docker flag.
    """

    if not isinstance(task_settings, dict):
        raise ValueError("BeyondSWE task settings must be an object")
    environment = task_settings.get("environment") or {}
    verifier = task_settings.get("verifier") or {}
    if not isinstance(environment, dict) or not isinstance(verifier, dict):
        raise ValueError("BeyondSWE environment and verifier settings must be objects")

    verifier_environment = verifier.get("environment")
    if verifier_environment is not None and not isinstance(verifier_environment, dict):
        raise ValueError("BeyondSWE verifier environment must be an object")

    if "network_mode" in verifier:
        policy = verifier.get("network_mode")
    elif isinstance(verifier_environment, dict):
        if "network_mode" in verifier_environment:
            policy = verifier_environment.get("network_mode")
        elif "allow_internet" in verifier_environment:
            policy = (
                "public" if verifier_environment.get("allow_internet") else "no-network"
            )
        else:
            policy = "public"
    elif "network_mode" in environment:
        policy = environment.get("network_mode")
    elif "allow_internet" in environment:
        policy = "public" if environment.get("allow_internet") else "no-network"
    else:
        policy = "public"

    policy_name, network = _beyondswe_docker_network(policy)
    return policy_name, _beyondswe_compose_network(task_dir) or network


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def run_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout_sec: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, float, bool]:
    environment = {**os.environ, **(env or {})}
    # Terminal evaluators run only after model access has ended. Do not expose
    # model-provider credentials to source benchmark code executed on the host.
    for name in list(environment):
        upper_name = name.upper()
        if upper_name.endswith(("_API_KEY", "_TOKEN", "_SECRET")):
            environment.pop(name, None)
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=environment,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        # Kill the whole evaluator-side process group. Killing only ``uv`` or
        # ``docker`` can leave descendants and evaluator containers running.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        return 124, stdout, stderr, time.monotonic() - started, True
    return proc.returncode, stdout, stderr, time.monotonic() - started, False


def remaining_timeout_sec(deadline: float) -> int:
    """Return a whole-second subprocess timeout within one evaluator deadline."""

    remaining = deadline - time.monotonic()
    return max(0, math.ceil(remaining))


def base_receipt(
    plan: dict[str, Any],
    *,
    status: str,
    infrastructure_failure: bool,
    task_passed: bool | None,
    tests_expected: int = 0,
    tests_collected: int = 0,
    tests_executed: int = 0,
    wall_time_sec: float = 0.0,
    return_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    details: dict[str, Any] | None = None,
    solve_sandbox_stopped_before_evaluator: bool = True,
) -> dict[str, Any]:
    return {
        "adapter_kind": plan.get("adapter_kind"),
        "adapter_version": plan.get("adapter_version"),
        "source_revision": plan.get("source_revision"),
        "execution_status": status,
        "infrastructure_failure": infrastructure_failure,
        "task_passed": task_passed,
        "passed": task_passed,
        "evaluator_identity": evaluator_identity(plan),
        "tests_expected": tests_expected,
        "tests_collected": tests_collected,
        "tests_executed": tests_executed,
        "pass_policy": (
            "reward-equals-one"
            if plan.get("adapter_kind") == "beyondswe_harbor"
            else (
                plan.get("pass_policy")
                or (plan.get("adapter_config") or {}).get("pass_policy")
                or "all-cases"
            )
        ),
        "wall_time_sec": round(wall_time_sec, 6),
        "return_code": return_code,
        "details": details or {},
        # Hidden evaluator material may be introduced only after the model-facing
        # solve has ended. SCBench runs after sandbox teardown; Harbor's
        # source-native shared verifier runs in the still-live solve container
        # after all worker/reporter/controller access has ended.
        "model_access_ended_before_evaluator": True,
        "solve_sandbox_stopped_before_evaluator": (
            solve_sandbox_stopped_before_evaluator
        ),
        # Private transport fields are removed by evaluate_with_plan after the
        # raw streams have been materialized in the evaluator-only artifact tree.
        "_adapter_stdout": stdout,
        "_adapter_stderr": stderr,
    }


def evaluate_with_plan(
    *,
    workspace: Path,
    output_dir: Path,
    plan: dict[str, Any],
    solve_sandbox: Any | None = None,
) -> dict[str, Any]:
    errors = validate_evaluator_plan(plan)
    if errors:
        raise ValueError("invalid evaluator plan: " + ",".join(errors))
    adapter_kind = plan["adapter_kind"]
    try:
        if adapter_kind == "scbench":
            if solve_sandbox is not None:
                raise ValueError("SCBench evaluation requires a sealed workspace")
            from .scbench import evaluate_scbench

            receipt = evaluate_scbench(workspace, output_dir, plan)
        elif adapter_kind == "beyondswe_harbor":
            if solve_sandbox is None:
                raise ValueError(
                    "BeyondSWE evaluation requires the live solve container"
                )
            from .beyondswe import evaluate_beyondswe_harbor

            receipt = evaluate_beyondswe_harbor(
                workspace,
                output_dir,
                plan,
                solve_sandbox=solve_sandbox,
            )
        else:  # pragma: no cover - plan validation closes this branch
            raise ValueError(f"unsupported evaluator adapter: {adapter_kind}")
    except Exception as exc:  # Keep unexpected adapter failures auditable.
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "adapter_internal_error.txt").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
        )
        receipt = base_receipt(
            plan,
            status="adapter_internal_error",
            infrastructure_failure=True,
            task_passed=None,
            stderr=f"{type(exc).__name__}: {exc}",
            details={"exception_type": type(exc).__name__},
        )
    if solve_sandbox is not None:
        receipt["solve_sandbox_stopped_before_evaluator"] = False
    raw_stdout = str(receipt.pop("_adapter_stdout", ""))
    raw_stderr = str(receipt.pop("_adapter_stderr", ""))
    # Early setup failures may return before an adapter creates its output tree.
    # Every receipt still archives and binds both raw streams.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "adapter_stdout.txt").write_text(raw_stdout, encoding="utf-8")
    (output_dir / "adapter_stderr.txt").write_text(raw_stderr, encoding="utf-8")
    receipt_errors = validate_evaluation_receipt(receipt, plan=plan)
    if receipt_errors:
        raise ValueError("adapter emitted invalid receipt: " + ",".join(receipt_errors))
    return receipt


__all__ = [
    "base_receipt",
    "beyondswe_solve_network",
    "evaluate_with_plan",
    "beyondswe_evaluator_network",
    "read_json",
    "remaining_timeout_sec",
    "run_argv",
    "scbench_evaluator_network",
]
