#!/usr/bin/env python3
"""Run a resumable Type II panel from a provider-neutral JSON plan.

The plan owns cases, seeds, conditions, models, endpoints, and budgets.  This
runner owns only orchestration: preflight, bounded concurrency, durable resume,
infrastructure-only retries, and an atomic status file.  API keys are named by
environment variable and are never copied into plans, commands, or artifacts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from looparena.paths import repository_root
from looparena.runtime.llm import default_worker_base_url

ROOT = repository_root()

from looparena.commands.type2_run import preflight_type2_case
from looparena.commands.validate_run import validate_run_dir
from looparena.harness.evaluator_protocol import derive_protocol_status
from looparena.runtime.non_adaptive_fixed_controller import (
    POLICY_ID as FIXED_POLICY_ID,
)
from looparena.runtime.non_adaptive_fixed_controller import (
    PROVIDER_KIND as FIXED_PROVIDER_KIND,
)
from looparena.runtime.source_identity import capture_harness_identity

OPERATIONAL_EXECUTION_FIELDS = {
    "concurrency",
    "continue_on_preflight_failure",
    "docker_poll_sec",
    "max_other_infrastructure_attempts",
    "max_provider_attempts",
    "retry_delay_sec",
}
PROVIDER_FAILURES = {
    "main_worker_provider_failure",
    "reporter_provider_failure",
    "controller_provider_failure",
}
RESUMABLE_TERMINAL_INFRASTRUCTURE_FAILURES = PROVIDER_FAILURES | {
    "main_worker_wall_time_exhausted",
    "reporter_wall_time_exhausted",
}
MODEL_PROVIDER_KIND = "model"
MODEL_TRANSPORT = "http"
FIXED_TRANSPORT = "local-deterministic"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ATTEMPT_OWNER_ENV = "LOOPARENA_ATTEMPT_OWNER_ID"
ATTEMPT_OWNER_LABEL = "io.looparena.attempt_owner"
PANEL_WRITER_LOCK = ".panel-writer.lock"
PROCESS_GROUP_GRACE_SEC = 10.0
STOP = threading.Event()
LOCK = threading.Lock()


class PanelWriterActiveError(RuntimeError):
    """Another panel runner already owns this output directory."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def acquire_panel_writer_lease(panel_root: Path) -> Any:
    """Acquire and return the panel's single-writer lock file."""
    lock_path = panel_root / PANEL_WRITER_LOCK
    stream = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        stream.seek(0)
        owner = stream.read().strip()
        stream.close()
        suffix = f" (pid={owner})" if owner else ""
        raise PanelWriterActiveError(
            f"panel output directory already has an active writer: {panel_root}{suffix}"
        ) from exc
    stream.seek(0)
    stream.truncate()
    stream.write(f"{os.getpid()}\n")
    stream.flush()
    return stream


def panel_writer_active(panel_root: Path) -> bool:
    """Return whether a live process currently holds the panel writer lease."""

    lock_path = panel_root / PANEL_WRITER_LOCK
    if not lock_path.is_file():
        return False
    with lock_path.open("r", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        else:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            return False


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _safe_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"{field} must match {SAFE_ID.pattern}")
    return value


def _environment_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not ENV_NAME.fullmatch(value):
        raise ValueError(f"{field} must be an environment-variable name")
    return value


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def _safe_error_text(error: object) -> str:
    """Keep diagnostics useful without persisting credentials."""

    text = str(error or "")
    for name, value in os.environ.items():
        if len(value) >= 8 and any(
            marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET")
        ):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"sk-[A-Za-z0-9._-]{12,}", "[REDACTED]", text)
    text = re.sub(
        r"(api[_-]?key|authorization|token)['\":= ]+[^\s,'\"]+",
        r"\1=[REDACTED]",
        text,
        flags=re.I,
    )
    return text[:300]


def _check_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} has unknown fields: {', '.join(unknown)}")


@dataclass(frozen=True)
class GatewayProfile:
    profile_id: str
    api_key_env: str
    base_url_env: str

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.profile_id,
            "api_key_env": self.api_key_env,
            "base_url_env": self.base_url_env,
        }


@dataclass(frozen=True)
class Condition:
    condition_id: str
    arm: str
    provider: str | None
    model: str | None
    transport: str | None
    credential_profile_id: str | None

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"id": self.condition_id, "arm": self.arm}
        if self.arm == "controlled":
            value["controller"] = {
                "model": self.model,
                "credential_profile_id": self.credential_profile_id,
            }
            if self.provider != MODEL_PROVIDER_KIND:
                value["controller"]["provider"] = self.provider
        return value


@dataclass(frozen=True)
class Execution:
    concurrency: int
    max_provider_attempts: int
    max_other_infrastructure_attempts: int
    retry_delay_sec: int
    docker_poll_sec: int
    scbench_runtime_profile: str
    worker_wall_time_sec: int
    reporter_wall_time_sec: int
    gateway_timeout_sec: int
    tool_timeout_sec: int
    continue_on_preflight_failure: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "max_provider_attempts": self.max_provider_attempts,
            "max_other_infrastructure_attempts": (
                self.max_other_infrastructure_attempts
            ),
            "retry_delay_sec": self.retry_delay_sec,
            "docker_poll_sec": self.docker_poll_sec,
            "scbench_runtime_profile": self.scbench_runtime_profile,
            "worker_wall_time_sec": self.worker_wall_time_sec,
            "reporter_wall_time_sec": self.reporter_wall_time_sec,
            "gateway_timeout_sec": self.gateway_timeout_sec,
            "tool_timeout_sec": self.tool_timeout_sec,
            "continue_on_preflight_failure": self.continue_on_preflight_failure,
        }


@dataclass(frozen=True)
class Canary:
    case_id: str
    condition_id: str
    seed: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "condition_id": self.condition_id,
            "seed": self.seed,
        }


@dataclass(frozen=True)
class PanelPlan:
    source: Path
    cohort: Path
    case_ids: tuple[str, ...]
    selection_stage: dict[str, str]
    seeds: tuple[int, ...]
    worker_model: str
    profiles: tuple[GatewayProfile, ...]
    conditions: tuple[Condition, ...]
    execution: Execution
    baseline_condition_id: str | None
    canary: Canary | None
    controller_gateway_profile: GatewayProfile | None = None

    def condition(self, condition_id: str) -> Condition:
        return next(
            condition
            for condition in self.conditions
            if condition.condition_id == condition_id
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "source_plan": str(self.source),
            "cohort_dir": str(self.cohort),
            "case_ids": list(self.case_ids),
            "selection_stage": dict(self.selection_stage),
            "seeds": list(self.seeds),
            "worker": {
                "model": self.worker_model,
            },
            "gateway_profiles": [profile.public_dict() for profile in self.profiles],
            "controller_gateway_profile": (
                self.controller_gateway_profile.public_dict()
                if self.controller_gateway_profile is not None
                else None
            ),
            "conditions": [condition.public_dict() for condition in self.conditions],
            "execution": self.execution.public_dict(),
            "baseline_condition_id": self.baseline_condition_id,
            "canary": self.canary.public_dict() if self.canary else None,
        }


@dataclass(frozen=True)
class Job:
    case_id: str
    condition_id: str
    seed: int

    @property
    def key(self) -> str:
        return f"{self.case_id}/{self.condition_id}/seed{self.seed}"


def load_plan(
    path: Path,
    *,
    case_override: str | None = None,
    concurrency_override: int | None = None,
    provider_attempts_override: int | None = None,
    other_attempts_override: int | None = None,
) -> PanelPlan:
    path = path.expanduser().resolve()
    raw = read_object(path)
    _check_keys(
        raw,
        {
            "cohort_dir",
            "cases",
            "seeds",
            "worker",
            "gateway_profiles",
            "controller_gateway_profile",
            "conditions",
            "execution",
            "baseline_condition_id",
            "canary",
        },
        "plan",
    )
    cohort_value = raw.get("cohort_dir")
    if not isinstance(cohort_value, str) or not cohort_value.strip():
        raise ValueError("cohort_dir must be a nonempty path")
    cohort = Path(cohort_value).expanduser()
    if not cohort.is_absolute():
        cohort = ROOT / cohort
    cohort = cohort.resolve()
    index = read_object(cohort / "CASE_INDEX.json")
    index_rows = index.get("cases")
    if not isinstance(index_rows, list) or not index_rows:
        raise ValueError("CASE_INDEX.json has no cases")
    indexed: list[str] = []
    selection_stage: dict[str, str] = {}
    for row in index_rows:
        if not isinstance(row, dict):
            raise ValueError("CASE_INDEX.json contains a non-object case")
        case_id = _safe_identifier(row.get("case_id"), "case_id")
        if case_id in indexed:
            raise ValueError(f"duplicate case_id: {case_id}")
        indexed.append(case_id)
        selection_stage[case_id] = str(row.get("selection_stage") or "unstratified")
    cases_value: object = (
        case_override.split(",") if case_override else raw.get("cases", "all")
    )
    if cases_value == "all":
        case_ids = indexed
    elif isinstance(cases_value, list):
        case_ids = [
            _safe_identifier(str(value).strip(), "cases[]")
            for value in cases_value
            if str(value).strip()
        ]
    else:
        raise ValueError('cases must be "all" or a list')
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ValueError("cases must be nonempty and unique")
    unknown_cases = sorted(set(case_ids) - set(indexed))
    if unknown_cases:
        raise ValueError("unknown cases: " + ", ".join(unknown_cases))
    missing_dirs = [
        case_id for case_id in case_ids if not (cohort / "cases" / case_id).is_dir()
    ]
    if missing_dirs:
        raise ValueError("missing case directories: " + ", ".join(missing_dirs))

    seeds_value = raw.get("seeds", [0])
    if not isinstance(seeds_value, list) or not seeds_value:
        raise ValueError("seeds must be a nonempty list")
    seeds = tuple(_nonnegative_int(value, "seeds[]") for value in seeds_value)
    if len(seeds) != len(set(seeds)):
        raise ValueError("seeds must be unique")

    worker = raw.get("worker")
    if not isinstance(worker, dict):
        raise ValueError("worker must be an object")
    _check_keys(worker, {"model"}, "worker")
    worker_model = str(worker.get("model") or "").strip()
    if not worker_model:
        raise ValueError("worker.model is required")
    profile_rows = raw.get("gateway_profiles")
    if not isinstance(profile_rows, list) or not profile_rows:
        raise ValueError("gateway_profiles must be a nonempty list")
    profiles: list[GatewayProfile] = []
    for row in profile_rows:
        if not isinstance(row, dict):
            raise ValueError("gateway_profiles[] must be an object")
        _check_keys(row, {"id", "api_key_env", "base_url_env"}, "gateway_profiles[]")
        profile = GatewayProfile(
            _safe_identifier(row.get("id"), "gateway_profiles[].id"),
            _environment_name(row.get("api_key_env"), "gateway_profiles[].api_key_env"),
            _environment_name(
                row.get("base_url_env"), "gateway_profiles[].base_url_env"
            ),
        )
        profiles.append(profile)
    if len({profile.profile_id for profile in profiles}) != len(profiles):
        raise ValueError("gateway profile IDs must be unique")

    controller_gateway_raw = raw.get("controller_gateway_profile")
    controller_gateway_profile: GatewayProfile | None = None
    if controller_gateway_raw is not None:
        if not isinstance(controller_gateway_raw, dict):
            raise ValueError("controller_gateway_profile must be an object or null")
        _check_keys(
            controller_gateway_raw,
            {"id", "api_key_env", "base_url_env"},
            "controller_gateway_profile",
        )
        controller_gateway_profile = GatewayProfile(
            _safe_identifier(
                controller_gateway_raw.get("id"),
                "controller_gateway_profile.id",
            ),
            _environment_name(
                controller_gateway_raw.get("api_key_env"),
                "controller_gateway_profile.api_key_env",
            ),
            _environment_name(
                controller_gateway_raw.get("base_url_env"),
                "controller_gateway_profile.base_url_env",
            ),
        )
        if controller_gateway_profile.profile_id in {
            profile.profile_id for profile in profiles
        }:
            raise ValueError(
                "controller_gateway_profile.id must differ from gateway profile IDs"
            )

    condition_rows = raw.get("conditions")
    if not isinstance(condition_rows, list) or not condition_rows:
        raise ValueError("conditions must be a nonempty list")
    conditions: list[Condition] = []
    for row in condition_rows:
        if not isinstance(row, dict):
            raise ValueError("conditions[] must be an object")
        _check_keys(row, {"id", "arm", "controller"}, "conditions[]")
        condition_id = _safe_identifier(row.get("id"), "conditions[].id")
        arm = str(row.get("arm") or "")
        if arm not in {"controlled", "no-control"}:
            raise ValueError(f"{condition_id}: invalid arm")
        controller = row.get("controller")
        if arm == "no-control":
            if controller not in (None, {}):
                raise ValueError(
                    f"{condition_id}: no-control cannot define a controller"
                )
            conditions.append(Condition(condition_id, arm, None, None, None, None))
            continue
        if not isinstance(controller, dict):
            raise ValueError(f"{condition_id}: controlled condition needs controller")
        _check_keys(
            controller,
            {
                "provider",
                "model",
                "credential_profile_id",
            },
            f"{condition_id}.controller",
        )
        provider = str(controller.get("provider") or MODEL_PROVIDER_KIND)
        if provider == FIXED_PROVIDER_KIND:
            extra = set(controller) - {"provider"}
            if extra:
                raise ValueError(
                    f"{condition_id}: fixed controller has no model settings"
                )
            conditions.append(
                Condition(
                    condition_id,
                    arm,
                    FIXED_PROVIDER_KIND,
                    FIXED_POLICY_ID,
                    FIXED_TRANSPORT,
                    None,
                )
            )
            continue
        if provider != MODEL_PROVIDER_KIND:
            raise ValueError(f"{condition_id}: invalid controller provider")
        model = str(controller.get("model") or "").strip()
        if not model:
            raise ValueError(f"{condition_id}: controller.model is required")
        controller_profile = controller.get("credential_profile_id")
        if controller_profile is not None:
            controller_profile = _safe_identifier(
                controller_profile, f"{condition_id}.controller.credential_profile_id"
            )
        if controller_profile is not None:
            raise ValueError(
                f"{condition_id}: Controller credentials are selected per job; "
                "omit controller.credential_profile_id"
            )
        conditions.append(
            Condition(
                condition_id,
                arm,
                MODEL_PROVIDER_KIND,
                model,
                MODEL_TRANSPORT,
                controller_profile,
            )
        )
    if len({condition.condition_id for condition in conditions}) != len(conditions):
        raise ValueError("condition IDs must be unique")

    execution_raw = raw.get("execution") or {}
    if not isinstance(execution_raw, dict):
        raise ValueError("execution must be an object")
    _check_keys(
        execution_raw,
        {
            "concurrency",
            "max_provider_attempts",
            "max_other_infrastructure_attempts",
            "retry_delay_sec",
            "docker_poll_sec",
            "scbench_runtime_profile",
            "worker_wall_time_sec",
            "reporter_wall_time_sec",
            "gateway_timeout_sec",
            "tool_timeout_sec",
            "continue_on_preflight_failure",
        },
        "execution",
    )
    concurrency = _positive_int(
        concurrency_override
        if concurrency_override is not None
        else execution_raw.get("concurrency", 2),
        "execution.concurrency",
    )
    provider_attempts = _positive_int(
        provider_attempts_override
        if provider_attempts_override is not None
        else execution_raw.get("max_provider_attempts", 3),
        "execution.max_provider_attempts",
    )
    other_attempts = _positive_int(
        other_attempts_override
        if other_attempts_override is not None
        else execution_raw.get("max_other_infrastructure_attempts", 1),
        "execution.max_other_infrastructure_attempts",
    )
    runtime_profile = str(
        execution_raw.get("scbench_runtime_profile") or "canonical-amd64"
    )
    if runtime_profile != "canonical-amd64":
        raise ValueError("invalid execution.scbench_runtime_profile")
    execution = Execution(
        concurrency=concurrency,
        max_provider_attempts=provider_attempts,
        max_other_infrastructure_attempts=other_attempts,
        retry_delay_sec=_nonnegative_int(
            execution_raw.get("retry_delay_sec", 120), "execution.retry_delay_sec"
        ),
        docker_poll_sec=_positive_int(
            execution_raw.get("docker_poll_sec", 15), "execution.docker_poll_sec"
        ),
        scbench_runtime_profile=runtime_profile,
        worker_wall_time_sec=_positive_int(
            execution_raw.get("worker_wall_time_sec", 7200),
            "execution.worker_wall_time_sec",
        ),
        reporter_wall_time_sec=_positive_int(
            execution_raw.get("reporter_wall_time_sec", 900),
            "execution.reporter_wall_time_sec",
        ),
        gateway_timeout_sec=_positive_int(
            execution_raw.get("gateway_timeout_sec", 900),
            "execution.gateway_timeout_sec",
        ),
        tool_timeout_sec=_positive_int(
            execution_raw.get("tool_timeout_sec", 120),
            "execution.tool_timeout_sec",
        ),
        continue_on_preflight_failure=_boolean(
            execution_raw.get("continue_on_preflight_failure", False),
            "execution.continue_on_preflight_failure",
        ),
    )

    baseline = raw.get("baseline_condition_id")
    if baseline is not None:
        baseline = _safe_identifier(baseline, "baseline_condition_id")
        matching = [
            condition for condition in conditions if condition.condition_id == baseline
        ]
        if not matching or matching[0].arm != "no-control":
            raise ValueError("baseline_condition_id must name a no-control condition")

    canary_raw = raw.get("canary")
    canary: Canary | None = None
    if canary_raw is not None:
        if not isinstance(canary_raw, dict):
            raise ValueError("canary must be an object or null")
        _check_keys(canary_raw, {"case_id", "condition_id", "seed"}, "canary")
        canary = Canary(
            _safe_identifier(canary_raw.get("case_id"), "canary.case_id"),
            _safe_identifier(canary_raw.get("condition_id"), "canary.condition_id"),
            _nonnegative_int(canary_raw.get("seed", seeds[0]), "canary.seed"),
        )
        if (
            canary.case_id not in case_ids
            or canary.condition_id
            not in {condition.condition_id for condition in conditions}
            or canary.seed not in seeds
        ):
            raise ValueError("canary must name one planned job")

    return PanelPlan(
        source=path,
        cohort=cohort,
        case_ids=tuple(case_ids),
        selection_stage={case_id: selection_stage[case_id] for case_id in case_ids},
        seeds=seeds,
        worker_model=worker_model,
        profiles=tuple(profiles),
        controller_gateway_profile=controller_gateway_profile,
        conditions=tuple(conditions),
        execution=execution,
        baseline_condition_id=baseline,
        canary=canary,
    )


def load_resolved_plan(path: Path) -> PanelPlan:
    """Rehydrate the public, secret-free plan recorded beside panel results."""

    path = path.expanduser().resolve()
    raw = read_object(path)
    execution_raw = raw["execution"]
    execution = Execution(
        concurrency=int(execution_raw["concurrency"]),
        max_provider_attempts=int(execution_raw["max_provider_attempts"]),
        max_other_infrastructure_attempts=int(
            execution_raw["max_other_infrastructure_attempts"]
        ),
        retry_delay_sec=int(execution_raw["retry_delay_sec"]),
        docker_poll_sec=int(execution_raw["docker_poll_sec"]),
        scbench_runtime_profile=str(execution_raw["scbench_runtime_profile"]),
        worker_wall_time_sec=int(execution_raw["worker_wall_time_sec"]),
        reporter_wall_time_sec=int(execution_raw["reporter_wall_time_sec"]),
        gateway_timeout_sec=int(execution_raw["gateway_timeout_sec"]),
        tool_timeout_sec=int(execution_raw["tool_timeout_sec"]),
        continue_on_preflight_failure=_boolean(
            execution_raw["continue_on_preflight_failure"],
            "execution.continue_on_preflight_failure",
        ),
    )
    conditions: list[Condition] = []
    for row in raw["conditions"]:
        controller = row.get("controller") or {}
        conditions.append(
            Condition(
                condition_id=str(row["id"]),
                arm=str(row["arm"]),
                provider=(
                    str(controller.get("provider") or MODEL_PROVIDER_KIND)
                    if controller
                    else None
                ),
                model=(str(controller["model"]) if controller else None),
                transport=(
                    FIXED_TRANSPORT
                    if str(controller.get("provider") or MODEL_PROVIDER_KIND)
                    == FIXED_PROVIDER_KIND
                    else MODEL_TRANSPORT
                )
                if controller
                else None,
                credential_profile_id=(
                    str(controller["credential_profile_id"])
                    if controller.get("credential_profile_id") is not None
                    else None
                ),
            )
        )
    canary_raw = raw.get("canary")
    return PanelPlan(
        source=Path(str(raw.get("source_plan") or path)),
        cohort=Path(str(raw["cohort_dir"])).resolve(),
        case_ids=tuple(str(value) for value in raw["case_ids"]),
        selection_stage={
            str(key): str(value)
            for key, value in (
                raw.get("selection_stage") or raw.get("difficulty") or {}
            ).items()
        },
        seeds=tuple(int(value) for value in raw["seeds"]),
        worker_model=str(raw["worker"]["model"]),
        profiles=tuple(
            GatewayProfile(
                str(row["id"]),
                str(row["api_key_env"]),
                str(row["base_url_env"]),
            )
            for row in raw["gateway_profiles"]
        ),
        controller_gateway_profile=(
            GatewayProfile(
                str(raw["controller_gateway_profile"]["id"]),
                str(raw["controller_gateway_profile"]["api_key_env"]),
                str(raw["controller_gateway_profile"]["base_url_env"]),
            )
            if isinstance(raw.get("controller_gateway_profile"), dict)
            else None
        ),
        conditions=tuple(conditions),
        execution=execution,
        baseline_condition_id=(
            str(raw["baseline_condition_id"])
            if raw.get("baseline_condition_id") is not None
            else None
        ),
        canary=(
            Canary(
                str(canary_raw["case_id"]),
                str(canary_raw["condition_id"]),
                int(canary_raw["seed"]),
            )
            if isinstance(canary_raw, dict)
            else None
        ),
    )


def jobs_for_plan(plan: PanelPlan) -> list[Job]:
    return [
        Job(case_id, condition.condition_id, seed)
        for case_id in plan.case_ids
        for condition in plan.conditions
        for seed in plan.seeds
    ]


def job_root(panel_root: Path, job: Job) -> Path:
    return panel_root / "results" / job.case_id / job.condition_id / f"seed{job.seed}"


def attempt_dirs(root: Path) -> list[Path]:
    return sorted(
        path for path in root.glob("attempt-[0-9][0-9][0-9]") if path.is_dir()
    )


def allocated_attempt_numbers(root: Path) -> list[int]:
    numbers = {int(path.name.removeprefix("attempt-")) for path in attempt_dirs(root)}
    for path in root.glob("attempt-[0-9][0-9][0-9].scheduler.json"):
        numbers.add(int(path.name.split(".", 1)[0].removeprefix("attempt-")))
    return sorted(numbers)


def next_attempt(root: Path) -> Path:
    numbers = allocated_attempt_numbers(root)
    return root / f"attempt-{max(numbers, default=0) + 1:03d}"


def attempt_owner_id(attempt: Path) -> str:
    """Return the non-secret Docker ownership label for one attempt."""

    return str(attempt.resolve())


def _manifest_identity_matches(
    manifest: dict[str, Any], plan: PanelPlan, job: Job
) -> bool:
    condition = plan.condition(job.condition_id)
    if (
        manifest.get("arm") != condition.arm
        or manifest.get("seed") != job.seed
        or (manifest.get("worker") or {}).get("model") != plan.worker_model
    ):
        return False
    if condition.arm == "no-control":
        return manifest.get("controller") in (None, {})
    controller = manifest.get("controller") or {}
    return (
        (controller.get("provider_kind") or MODEL_PROVIDER_KIND) == condition.provider
        and controller.get("model") == condition.model
        and controller.get("transport") == condition.transport
    )


def valid_attempt(path: Path, plan: PanelPlan, job: Job) -> dict[str, Any] | None:
    manifest_path = path / "run_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = read_object(manifest_path)
        validation = validate_run_dir(path, require_terminal_evaluator=True)
    except Exception:
        return None
    if not (
        validation.get("ok") is True
        and validation.get("infrastructure_valid") is True
        and isinstance(validation.get("task_success_at_budget"), bool)
        and _manifest_identity_matches(manifest, plan, job)
    ):
        return None
    protocol = derive_protocol_status(manifest)
    return {
        "attempt": str(path),
        "evaluation_state": "completed",
        "infrastructure_valid": True,
        "task_passed": bool(
            validation["task_success_at_budget"]
            and protocol["protocol_valid"]
            and protocol["submit_protocol_satisfied"]
        ),
        "protocol_valid": protocol["protocol_valid"],
        "submit_protocol_satisfied": protocol["submit_protocol_satisfied"],
        "termination_reason": manifest.get("termination_reason"),
        "worker_turns": manifest.get("main_worker_turns"),
        "reporter_turns": manifest.get("reporter_turns"),
        "controller_calls": manifest.get("controller_calls"),
    }


def latest_valid(root: Path, plan: PanelPlan, job: Job) -> dict[str, Any] | None:
    for path in attempt_dirs(root):
        result = valid_attempt(path, plan, job)
        if result is not None:
            return result
    return None


def attempt_termination(path: Path) -> str:
    for name in ("solve_manifest.json", "run_manifest.json"):
        try:
            value = read_object(path / name).get("termination_reason")
        except Exception:
            continue
        if isinstance(value, str) and value:
            return value
    return ""


def model_work_started(path: Path) -> bool:
    return any(
        candidate.is_file() and candidate.stat().st_size > 0
        for candidate in (
            path / "main_worker_slices.jsonl",
            path / "reporter_runs.jsonl",
            path / "controller_results.jsonl",
        )
    )


def safe_resume_source(path: Path, plan: PanelPlan, job: Job) -> bool:
    try:
        checkpoint = read_object(path / "recovery_checkpoint.json")
    except Exception:
        return False
    if checkpoint.get("safe_to_resume") is not True:
        return False
    manifest_path = path / "solve_manifest.json"
    if manifest_path.is_file():
        try:
            if not _manifest_identity_matches(read_object(manifest_path), plan, job):
                return False
        except Exception:
            return False
    if attempt_termination(path) not in RESUMABLE_TERMINAL_INFRASTRUCTURE_FAILURES:
        try:
            state = read_object(path / "attempt_state.json")
        except Exception:
            return False
        if state.get("status") != "interrupted":
            return False
    workspace = path.parent / f"{path.name}.workspace"
    return workspace.is_dir()


def _failure_counts(root: Path) -> tuple[int, int]:
    provider = other = 0
    for path in attempt_dirs(root):
        if attempt_termination(path) in PROVIDER_FAILURES:
            provider += 1
        else:
            other += 1
    for path in root.glob("attempt-[0-9][0-9][0-9].scheduler.json"):
        attempt = root / path.name.removesuffix(".scheduler.json")
        if not attempt.is_dir():
            other += 1
    return provider, other


def _retry_decision(
    root: Path, execution: Execution, plan: PanelPlan, job: Job
) -> tuple[bool, Path | None, str]:
    attempts = attempt_dirs(root)
    if not attempts:
        _, other_count = _failure_counts(root)
        if other_count >= execution.max_other_infrastructure_attempts:
            return False, None, "infrastructure_attempt_limit"
        return True, None, "fresh"
    latest = attempts[-1]
    provider_count, other_count = _failure_counts(root)
    termination = attempt_termination(latest)
    if termination in PROVIDER_FAILURES:
        if provider_count >= execution.max_provider_attempts:
            return False, None, "provider_attempt_limit"
        if safe_resume_source(latest, plan, job):
            return True, latest, "safe_provider_checkpoint"
        if not model_work_started(latest):
            return True, None, "provider_failure_before_model_work"
        return False, None, "provider_failure_without_safe_checkpoint"
    if other_count >= execution.max_other_infrastructure_attempts:
        return False, None, "infrastructure_attempt_limit"
    if safe_resume_source(latest, plan, job):
        return True, latest, "safe_interrupted_checkpoint"
    return True, None, "fresh_infrastructure_retry"


def _profile_pair(
    plan: PanelPlan, job: Job, attempt_number: int
) -> tuple[GatewayProfile, GatewayProfile | None]:
    initial_slot = sum(job.key.encode("utf-8")) % len(plan.profiles)
    slot = (initial_slot + attempt_number - 1) % len(plan.profiles)
    primary = plan.profiles[slot]
    fallback = (
        plan.profiles[(slot + 1) % len(plan.profiles)]
        if len(plan.profiles) > 1
        else None
    )
    return primary, fallback


def _profile_values(profile: GatewayProfile) -> tuple[str, str]:
    key = os.environ.get(profile.api_key_env, "")
    base_url = os.environ.get(profile.base_url_env, "").rstrip("/")
    if not base_url and profile.base_url_env == "OPENAI_BASE_URL":
        base_url = default_worker_base_url().rstrip("/")
    if not key:
        raise RuntimeError(
            f"gateway credential environment is missing for {profile.profile_id}"
        )
    if not base_url:
        raise RuntimeError(
            f"gateway base URL environment is missing for {profile.profile_id}"
        )
    return key, base_url


def subprocess_environment(
    primary: GatewayProfile,
    fallback: GatewayProfile | None,
    controller_profile: GatewayProfile | None = None,
) -> tuple[dict[str, str], str, str | None]:
    primary_key, base_url = _profile_values(primary)
    environment = os.environ.copy()
    environment["OPENAI_API_KEY"] = primary_key
    environment["OPENAI_BASE_URL"] = base_url
    environment["LOOPARENA_CREDENTIAL_PROFILE"] = primary.profile_id
    environment["LOOPARENA_CREDENTIAL_PROFILE_ID"] = primary.profile_id
    for name in (
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "LOOPARENA_GATEWAY_FALLBACK_API_KEY",
        "LOOPARENA_GATEWAY_FALLBACK_PROFILE_ID",
    ):
        environment.pop(name, None)
    if fallback is not None:
        fallback_key, fallback_base_url = _profile_values(fallback)
        if fallback_base_url != base_url:
            raise RuntimeError("gateway fallback profiles must use the same endpoint")
        environment["LOOPARENA_GATEWAY_FALLBACK_API_KEY"] = fallback_key
        environment["LOOPARENA_GATEWAY_FALLBACK_PROFILE_ID"] = fallback.profile_id
    controller_base_url: str | None = None
    if controller_profile is not None:
        controller_key, controller_base_url = _profile_values(controller_profile)
        environment[controller_profile.api_key_env] = controller_key
        environment[controller_profile.base_url_env] = controller_base_url
    return environment, base_url, controller_base_url


def build_case_command(
    *,
    plan: PanelPlan,
    job: Job,
    attempt: Path,
    assets_root: Path,
    primary: GatewayProfile,
    base_url: str,
    controller_profile: GatewayProfile | None = None,
    controller_base_url: str | None = None,
    resume_source: Path | None = None,
) -> list[str]:
    condition = plan.condition(job.condition_id)
    command = [
        sys.executable,
        "-m",
        "looparena.commands.type2_run",
        "--case-dir",
        str(plan.cohort / "cases" / job.case_id),
        "--arm",
        condition.arm,
        "--out-dir",
        str(attempt),
        "--assets-root",
        str(assets_root),
        "--seed",
        str(job.seed),
        "--worker-model",
        plan.worker_model,
        "--base-url",
        base_url,
        "--credential-profile-id",
        primary.profile_id,
        "--scbench-runtime-profile",
        plan.execution.scbench_runtime_profile,
        "--worker-wall-time-sec",
        str(plan.execution.worker_wall_time_sec),
        "--reporter-wall-time-sec",
        str(plan.execution.reporter_wall_time_sec),
        "--gateway-timeout-sec",
        str(plan.execution.gateway_timeout_sec),
        "--tool-timeout-sec",
        str(plan.execution.tool_timeout_sec),
    ]
    if condition.arm == "controlled":
        command.extend(["--controller-provider", str(condition.provider)])
        if condition.provider == MODEL_PROVIDER_KIND:
            credential_profile_id = (
                condition.credential_profile_id or primary.profile_id
            )
            if controller_profile is not None:
                credential_profile_id = controller_profile.profile_id
            command.extend(
                [
                    "--controller-model",
                    str(condition.model),
                    "--controller-credential-profile-id",
                    credential_profile_id,
                ]
            )
            gateway_controller = controller_profile or primary
            command.extend(
                [
                    "--controller-base-url",
                    controller_base_url or base_url,
                    "--controller-api-key-env",
                    gateway_controller.api_key_env,
                ]
            )
    if resume_source is not None:
        command.extend(["--resume-from-attempt", str(resume_source)])
    return command


def redact_command_endpoints(command: list[str]) -> list[str]:
    """Return scheduler-safe argv without persisting provider endpoints."""

    redacted = list(command)
    for option in ("--base-url", "--controller-base-url"):
        for index, value in enumerate(redacted[:-1]):
            if value == option:
                redacted[index + 1] = "[REDACTED]"
    return redacted


def docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_process_group(process: subprocess.Popen[str], signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        pass


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Stop one job and its descendants, escalating only after a short grace."""

    _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_GROUP_GRACE_SEC
    while _process_group_exists(process.pid) and time.monotonic() < deadline:
        process.poll()
        time.sleep(0.1)
    if _process_group_exists(process.pid):
        _signal_process_group(process, signal.SIGKILL)


def _cleanup_attempt_containers(owner_id: str) -> None:
    """Remove containers carrying this attempt's exact label, if any remain."""

    try:
        listed = subprocess.run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label={ATTEMPT_OWNER_LABEL}={owner_id}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        container_ids = listed.stdout.split() if listed.returncode == 0 else []
        if container_ids:
            subprocess.run(
                ["docker", "rm", "-f", *container_ids],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _case_preflight(
    case_dir: Path, assets_root: Path, runtime_profile: str
) -> dict[str, Any]:
    try:
        details = preflight_type2_case(
            case_dir,
            assets_root=assets_root,
            scbench_runtime_profile=runtime_profile,
        )
    except Exception as exc:
        return {
            "case_id": case_dir.name,
            "ready": False,
            "error_type": type(exc).__name__,
            "reason": _safe_error_text(exc),
        }
    return {"case_id": case_dir.name, "ready": True, **details}


class PanelRunner:
    def __init__(self, plan: PanelPlan, panel_root: Path, assets_root: Path) -> None:
        self.plan = plan
        self.panel_root = panel_root
        self.assets_root = assets_root
        self.records: dict[str, dict[str, Any]] = {}
        self.stage = "initializing"
        self.started_at = now()
        self.status_path = panel_root / "status.json"

    def record(self, job: Job, **values: Any) -> None:
        with LOCK:
            current = dict(self.records.get(job.key) or {})
            current.update(values)
            current["updated_at"] = now()
            self.records[job.key] = current
            self._write_status_locked()

    def set_stage(self, stage: str) -> None:
        with LOCK:
            self.stage = stage
            self._write_status_locked()

    def _write_status_locked(self) -> None:
        rows = list(self.records.values())
        atomic_json(
            self.status_path,
            {
                "stage": self.stage,
                "started_at": self.started_at,
                "updated_at": now(),
                "configuration": {
                    key: value
                    for key, value in self.plan.execution.public_dict().items()
                    if key in OPERATIONAL_EXECUTION_FIELDS
                },
                "planned_runs": len(self.records),
                "completed_runs": sum(row.get("status") == "completed" for row in rows),
                "running_runs": sum(row.get("status") == "running" for row in rows),
                "pending_runs": sum(row.get("status") == "pending" for row in rows),
                "waiting_for_docker": sum(
                    row.get("status") == "waiting_for_docker" for row in rows
                ),
                "infrastructure_unresolved": sum(
                    row.get("status") == "infrastructure_unresolved" for row in rows
                ),
                "preflight_blocked": sum(
                    row.get("status") == "preflight_blocked" for row in rows
                ),
                "records": dict(sorted(self.records.items())),
            },
        )

    def wait_for_docker(self, job: Job) -> bool:
        while not STOP.is_set():
            if docker_available():
                return True
            self.record(job, status="waiting_for_docker")
            STOP.wait(self.plan.execution.docker_poll_sec)
        return False

    def run_job(self, job: Job) -> dict[str, Any]:
        root = job_root(self.panel_root, job)
        root.mkdir(parents=True, exist_ok=True)
        existing = latest_valid(root, self.plan, job)
        if existing is not None:
            self.record(job, status="completed", reused=True, **existing)
            return self.records[job.key]
        while not STOP.is_set():
            allowed, resume_source, reason = _retry_decision(
                root, self.plan.execution, self.plan, job
            )
            if not allowed:
                self.record(
                    job,
                    status="infrastructure_unresolved",
                    attempts=len(attempt_dirs(root)),
                    reason=reason,
                    latest_attempt=(
                        str(attempt_dirs(root)[-1]) if attempt_dirs(root) else None
                    ),
                )
                return self.records[job.key]
            if not self.wait_for_docker(job):
                break
            attempt = next_attempt(root)
            attempt_number = int(attempt.name.removeprefix("attempt-"))
            primary, fallback = _profile_pair(self.plan, job, attempt_number)
            environment, base_url, controller_base_url = subprocess_environment(
                primary,
                fallback,
                self.plan.controller_gateway_profile,
            )
            owner_id = attempt_owner_id(attempt)
            environment[ATTEMPT_OWNER_ENV] = owner_id
            command = build_case_command(
                plan=self.plan,
                job=job,
                attempt=attempt,
                assets_root=self.assets_root,
                primary=primary,
                base_url=base_url,
                controller_profile=self.plan.controller_gateway_profile,
                controller_base_url=controller_base_url,
                resume_source=resume_source,
            )
            scheduler = {
                "job": job.key,
                "attempt": attempt.name,
                "credential_profile": primary.profile_id,
                "fallback_credential_profile": fallback.profile_id
                if fallback
                else None,
                "controller_gateway_credential_profile": (
                    self.plan.controller_gateway_profile.profile_id
                    if self.plan.controller_gateway_profile is not None
                    else None
                ),
                "attempt_owner_label": ATTEMPT_OWNER_LABEL,
                "attempt_owner_id": owner_id,
                "resume_from_attempt": str(resume_source) if resume_source else None,
                "retry_reason": reason,
                "started_at": now(),
                "command": redact_command_endpoints(command),
            }
            atomic_json(root / f"{attempt.name}.scheduler.json", scheduler)
            log_path = root / f"{attempt.name}.log"
            self.record(
                job,
                status="running",
                attempt=str(attempt),
                attempt_number=attempt_number,
                credential_profile=primary.profile_id,
                fallback_credential_profile=fallback.profile_id if fallback else None,
                resume_from_attempt=str(resume_source) if resume_source else None,
                log=str(log_path),
            )
            with log_path.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                while process.poll() is None:
                    if STOP.wait(0.2):
                        _terminate_process_group(process)
                        break
                return_code = process.wait()
                if STOP.is_set():
                    _cleanup_attempt_containers(owner_id)
            scheduler.update(
                {
                    "finished_at": now(),
                    "return_code": return_code,
                    "attempt_directory_created": attempt.is_dir(),
                }
            )
            atomic_json(root / f"{attempt.name}.scheduler.json", scheduler)
            valid = valid_attempt(attempt, self.plan, job)
            if valid is not None:
                self.record(
                    job,
                    status="completed",
                    reused=False,
                    return_code=return_code,
                    **valid,
                )
                return self.records[job.key]
            self.record(
                job,
                status="retrying",
                return_code=return_code,
                attempt=str(attempt),
                termination_reason=attempt_termination(attempt),
            )
            if self.plan.execution.retry_delay_sec:
                STOP.wait(self.plan.execution.retry_delay_sec)
        self.record(job, status="interrupted")
        return self.records[job.key]


def _signal_handler(signum: int, _frame: object) -> None:
    del signum
    STOP.set()


def _resolved_document(plan: PanelPlan) -> dict[str, Any]:
    document = plan.public_dict()
    document.update(capture_harness_identity(ROOT))
    document["planned_run_count"] = len(jobs_for_plan(plan))
    return document


def _experimental_identity(document: dict[str, Any]) -> dict[str, Any]:
    identity = json.loads(json.dumps(document))
    identity.pop("source_plan", None)
    execution = identity.get("execution") or {}
    for field in OPERATIONAL_EXECUTION_FIELDS:
        execution.pop(field, None)
    return identity


def _materialize_plan(panel_root: Path, document: dict[str, Any]) -> None:
    target = panel_root / "resolved_plan.json"
    if target.is_file():
        existing = read_object(target)
        if _experimental_identity(existing) != _experimental_identity(document):
            raise RuntimeError(
                "existing resolved_plan.json has different experimental semantics; "
                "use a new output directory"
            )
    else:
        atomic_json(target, document)
    append_jsonl(
        panel_root / "orchestration_history.jsonl",
        {
            "started_at": now(),
            "source_plan": document.get("source_plan"),
            "harness_git_head": document.get("harness_git_head"),
            "execution": {
                key: value
                for key, value in (document.get("execution") or {}).items()
                if key in OPERATIONAL_EXECUTION_FIELDS
            },
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--assets-root",
        type=Path,
        default=os.environ.get("LOOPARENA_ASSETS_ROOT"),
    )
    parser.add_argument(
        "--cases", help="Comma-separated subset; default comes from plan"
    )
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--max-provider-attempts", type=int)
    parser.add_argument("--max-other-infrastructure-attempts", type=int)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-canary", action="store_true")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow a dirty harness worktree; formal runs should not use this.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = load_plan(
            args.plan,
            case_override=args.cases,
            concurrency_override=args.concurrency,
            provider_attempts_override=args.max_provider_attempts,
            other_attempts_override=args.max_other_infrastructure_attempts,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid panel plan: {exc}") from exc
    document = _resolved_document(plan)
    if document["harness_worktree_dirty"] and not args.allow_dirty:
        raise SystemExit("harness worktree is dirty; commit it or pass --allow-dirty")
    if args.dry_run:
        print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.assets_root is None:
        raise SystemExit("set LOOPARENA_ASSETS_ROOT or pass --assets-root")
    assets_root = args.assets_root.expanduser().resolve()
    if not assets_root.is_dir():
        raise SystemExit(f"assets root is not a directory: {assets_root}")
    panel_root = args.out_dir.expanduser().resolve()
    panel_root.mkdir(parents=True, exist_ok=True)
    try:
        # Keeping this local handle alive holds the advisory lock for the run.
        _writer_lease = acquire_panel_writer_lease(panel_root)
    except PanelWriterActiveError as exc:
        raise SystemExit(str(exc)) from exc
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    try:
        profile_values = [_profile_values(profile) for profile in plan.profiles]
        if plan.controller_gateway_profile is not None:
            _profile_values(plan.controller_gateway_profile)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if len({key for key, _ in profile_values}) != len(profile_values):
        raise SystemExit("gateway profiles must resolve to distinct credentials")
    profile_endpoints = {base_url for _, base_url in profile_values}
    if len(profile_endpoints) != 1:
        raise SystemExit(
            "gateway profiles must share one endpoint; do not mix providers as fallback"
        )
    _materialize_plan(panel_root, document)

    if args.preflight_only and not docker_available():
        preflight_document = {
            "updated_at": now(),
            "stage": "docker_unavailable",
            "docker_ready": False,
            "cases": [],
        }
        atomic_json(panel_root / "preflight.json", preflight_document)
        print(
            json.dumps(preflight_document, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return 2

    while not STOP.is_set() and not docker_available():
        atomic_json(
            panel_root / "preflight.json",
            {
                "updated_at": now(),
                "stage": "waiting_for_docker",
                "docker_ready": False,
            },
        )
        STOP.wait(plan.execution.docker_poll_sec)
    if STOP.is_set():
        return 130
    preflight = [
        _case_preflight(
            plan.cohort / "cases" / case_id,
            assets_root,
            plan.execution.scbench_runtime_profile,
        )
        for case_id in plan.case_ids
    ]
    preflight_document = {
        "updated_at": now(),
        "docker_ready": True,
        "cases": preflight,
    }
    atomic_json(panel_root / "preflight.json", preflight_document)
    case_blocked = {row["case_id"] for row in preflight if row.get("ready") is not True}
    if args.preflight_only:
        print(
            json.dumps(preflight_document, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return 0 if not case_blocked else 2
    if case_blocked and not plan.execution.continue_on_preflight_failure:
        raise SystemExit("case preflight failed: " + ", ".join(sorted(case_blocked)))

    runner = PanelRunner(plan, panel_root, assets_root)
    all_jobs = jobs_for_plan(plan)
    for job in all_jobs:
        runner.records[job.key] = {
            "status": "preflight_blocked" if job.case_id in case_blocked else "pending",
            "updated_at": now(),
        }
    runner.set_stage("preflight_complete")
    ready_jobs = [job for job in all_jobs if job.case_id not in case_blocked]
    canary_job = None
    if plan.canary is not None and not args.no_canary:
        canary_job = Job(
            plan.canary.case_id,
            plan.canary.condition_id,
            plan.canary.seed,
        )
        if canary_job not in ready_jobs:
            raise SystemExit("canary was blocked by case preflight")
        runner.set_stage("canary_running")
        canary_result = runner.run_job(canary_job)
        if canary_result.get("status") != "completed":
            runner.set_stage("canary_failed")
            return 2
        ready_jobs.remove(canary_job)

    runner.set_stage("panel_running")
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=plan.execution.concurrency
    ) as pool:
        futures = {pool.submit(runner.run_job, job): job for job in ready_jobs}
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                future.result()
            except Exception as exc:
                runner.record(
                    job,
                    status="orchestration_error",
                    error_type=type(exc).__name__,
                    reason=_safe_error_text(exc),
                )
    if STOP.is_set():
        runner.set_stage("interrupted")
        return 130
    terminal = [row.get("status") for row in runner.records.values()]
    if all(status == "completed" for status in terminal):
        runner.set_stage("complete")
        return 0
    runner.set_stage("complete_with_infrastructure_skips")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
