"""Run a resumable Type III panel from a small JSON plan."""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from looparena.paths import repository_root
from looparena.runtime.llm import DEFAULT_API_KEY_ENV, _redact

ROOT = repository_root()
PROVIDER_FAILURES = {
    "main_worker_provider_failure",
    "reporter_provider_failure",
    "controller_provider_failure",
}


def _check_keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} has unknown fields: {', '.join(unknown)}")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _safe_error_text(error: object) -> str:
    return _redact(str(error or ""))[:300]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Job:
    case_id: str
    seed: int

    @property
    def key(self) -> str:
        return f"{self.case_id}/seed{self.seed}"


@dataclass(frozen=True)
class Plan:
    source: Path
    cohort: Path
    assets_root: Path
    case_ids: tuple[str, ...]
    seeds: tuple[int, ...]
    arm: str
    worker_model: str
    controller: dict[str, Any]
    execution: dict[str, int | str]

    def public(self) -> dict[str, Any]:
        return {
            "source_plan": str(self.source),
            "cohort_dir": str(self.cohort),
            "assets_root": str(self.assets_root),
            "case_ids": list(self.case_ids),
            "seeds": list(self.seeds),
            "arm": self.arm,
            "worker_model": self.worker_model,
            "controller": self.controller if self.arm == "controlled" else None,
            "execution": self.execution,
        }


def load_plan(path: Path) -> Plan:
    path = path.expanduser().resolve()
    raw = _read(path)
    _check_keys(
        raw,
        {
            "cohort_dir",
            "assets_root",
            "cases",
            "seeds",
            "arm",
            "worker_model",
            "controller",
            "execution",
        },
        "plan",
    )
    cohort = Path(str(raw.get("cohort_dir") or "benchmarks/type3")).expanduser()
    if not cohort.is_absolute():
        cohort = ROOT / cohort
    cohort = cohort.resolve()
    index = _read(cohort / "CASE_INDEX.json")
    indexed = [str(row["case_id"]) for row in index.get("cases") or []]
    selected = raw.get("cases", "all")
    case_ids = indexed if selected == "all" else [str(value) for value in selected]
    if not case_ids or len(case_ids) != len(set(case_ids)):
        raise ValueError("cases must be nonempty and unique")
    unknown = sorted(set(case_ids) - set(indexed))
    if unknown:
        raise ValueError("unknown Type III cases: " + ", ".join(unknown))

    seeds = tuple(raw.get("seeds") or [0])
    if (
        not seeds
        or len(seeds) != len(set(seeds))
        or any(
            not isinstance(seed, int) or isinstance(seed, bool) or seed < 0
            for seed in seeds
        )
    ):
        raise ValueError("seeds must be unique nonnegative integers")
    arm = str(raw.get("arm") or "")
    if arm not in {"controlled", "no-control"}:
        raise ValueError("arm must be controlled or no-control")
    worker_model = str(raw.get("worker_model") or "").strip()
    if not worker_model:
        raise ValueError("worker_model is required")
    controller = raw.get("controller") or {}
    if arm == "controlled" and not isinstance(controller, dict):
        raise ValueError("controller must be an object")
    if arm == "no-control" and controller:
        raise ValueError("no-control cannot define a controller")
    if arm == "controlled":
        provider = str(controller.get("provider") or "model")
        if provider == "model":
            _check_keys(controller, {"provider", "model", "api_key_env"}, "controller")
            if not str(controller.get("model") or "").strip():
                raise ValueError("controller.model is required")
        elif provider == "non-adaptive-fixed":
            _check_keys(controller, {"provider"}, "controller")
        else:
            raise ValueError("controller.provider is invalid")

    execution = raw.get("execution") or {}
    concurrency = execution.get("concurrency", 1)
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or concurrency < 1
    ):
        raise ValueError("execution.concurrency must be positive")
    defaults: dict[str, int | str] = {
        "concurrency": concurrency,
        "scbench_runtime_profile": str(
            execution.get("scbench_runtime_profile") or "canonical-amd64"
        ),
        "worker_wall_time_sec": int(execution.get("worker_wall_time_sec", 7200)),
        "reporter_wall_time_sec": int(execution.get("reporter_wall_time_sec", 900)),
        "gateway_timeout_sec": int(execution.get("gateway_timeout_sec", 900)),
        "tool_timeout_sec": int(execution.get("tool_timeout_sec", 120)),
    }
    assets_root = (
        Path(str(raw.get("assets_root") or "~/.cache/looparena/assets"))
        .expanduser()
        .resolve()
    )
    return Plan(
        path,
        cohort,
        assets_root,
        tuple(case_ids),
        seeds,
        arm,
        worker_model,
        dict(controller),
        defaults,
    )


def jobs(plan: Plan) -> list[Job]:
    return [Job(case_id, seed) for case_id in plan.case_ids for seed in plan.seeds]


def run_dir(panel_root: Path, job: Job) -> Path:
    return panel_root / "results" / job.case_id / f"seed{job.seed}"


def classify(path: Path) -> str:
    manifest_path = path / "run_manifest.json"
    if not manifest_path.is_file():
        return "pending" if not path.exists() else "infrastructure_invalid"
    manifest = _read(manifest_path)
    if (
        manifest.get("status") == "completed" and manifest.get("task_passed") is True
    ) or (manifest.get("status") == "failed" and manifest.get("task_passed") is False):
        return "valid"
    steps = manifest.get("steps") or []
    if manifest.get("status") == "running" and steps and (path / "workspace").is_dir():
        return "resumable"
    if manifest.get("status") == "infrastructure_invalid" and steps:
        step_id = str(steps[-1].get("step_id") or "")
        child_dir = path / "checkpoints" / step_id
        if (
            not (child_dir / "run_manifest.json").is_file()
            or not (child_dir / "recovery_checkpoint.json").is_file()
        ):
            return "infrastructure_invalid"
        child = _read(child_dir / "run_manifest.json")
        checkpoint = _read(child_dir / "recovery_checkpoint.json")
        if (
            child.get("termination_reason") in PROVIDER_FAILURES
            and checkpoint.get("safe_to_resume") is True
        ):
            return "resumable"
    return "infrastructure_invalid"


def build_command(
    plan: Plan, panel_root: Path, job: Job, *, preflight: bool
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "looparena.commands.type3_run",
        "--case-dir",
        str(plan.cohort / "cases" / job.case_id),
        "--assets-root",
        str(plan.assets_root),
        "--arm",
        plan.arm,
        "--seed",
        str(job.seed),
        "--worker-model",
        plan.worker_model,
        "--scbench-runtime-profile",
        str(plan.execution["scbench_runtime_profile"]),
        "--worker-wall-time-sec",
        str(plan.execution["worker_wall_time_sec"]),
        "--reporter-wall-time-sec",
        str(plan.execution["reporter_wall_time_sec"]),
        "--gateway-timeout-sec",
        str(plan.execution["gateway_timeout_sec"]),
        "--tool-timeout-sec",
        str(plan.execution["tool_timeout_sec"]),
    ]
    if preflight:
        command.append("--preflight-only")
    else:
        output = run_dir(panel_root, job)
        command.extend(["--out-dir", str(output)])
        if classify(output) == "resumable":
            command.append("--resume-existing")
    if plan.arm == "controlled":
        provider = str(plan.controller.get("provider") or "model")
        command.extend(["--controller-provider", provider])
        if provider == "model":
            command.extend(
                [
                    "--controller-model",
                    str(plan.controller["model"]),
                    "--controller-api-key-env",
                    str(plan.controller.get("api_key_env") or DEFAULT_API_KEY_ENV),
                ]
            )
    return command


def _run(command: list[str]) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    error = (result.stderr or result.stdout).strip()
    return result.returncode, error[-1000:]


def snapshot(
    plan: Plan, panel_root: Path, active: set[str] | None = None
) -> dict[str, Any]:
    active = active or set()
    records = []
    for job in jobs(plan):
        state = "running" if job.key in active else classify(run_dir(panel_root, job))
        records.append({"case_id": job.case_id, "seed": job.seed, "state": state})
    return {
        "updated_at": _now(),
        "counts": {
            state: sum(record["state"] == state for record in records)
            for state in (
                "valid",
                "running",
                "resumable",
                "infrastructure_invalid",
                "pending",
            )
        },
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    plan = load_plan(args.plan)
    panel_root = args.out_dir.expanduser().resolve()
    panel_root.mkdir(parents=True, exist_ok=True)
    lock = (panel_root / ".writer.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit(f"active Type III panel writer: {panel_root}") from exc
    _write(panel_root / "resolved_plan.json", plan.public())

    if args.preflight_only:
        for case_id in plan.case_ids:
            code, error = _run(
                build_command(
                    plan, panel_root, Job(case_id, plan.seeds[0]), preflight=True
                )
            )
            if code:
                raise SystemExit(
                    f"preflight failed for {case_id}: {_safe_error_text(error)}"
                )
        print(json.dumps({"preflight": "passed", "cases": len(plan.case_ids)}))
        return 0

    for case_id in plan.case_ids:
        code, error = _run(
            build_command(plan, panel_root, Job(case_id, plan.seeds[0]), preflight=True)
        )
        if code:
            raise SystemExit(
                f"preflight failed for {case_id}: {_safe_error_text(error)}"
            )

    runnable = [
        job
        for job in jobs(plan)
        if classify(run_dir(panel_root, job)) in {"pending", "resumable"}
    ]
    active: set[str] = set()
    state_lock = threading.Lock()

    def run_one(job: Job) -> tuple[int, str]:
        with state_lock:
            active.add(job.key)
            _write(panel_root / "status.json", snapshot(plan, panel_root, active))
        code, error = _run(build_command(plan, panel_root, job, preflight=False))
        with state_lock:
            active.remove(job.key)
            if code and classify(run_dir(panel_root, job)) not in {
                "valid",
                "resumable",
            }:
                output = run_dir(panel_root, job)
                output.mkdir(parents=True, exist_ok=True)
                (output / "panel_error.txt").write_text(
                    _safe_error_text(error) + "\n", encoding="utf-8"
                )
            _write(panel_root / "status.json", snapshot(plan, panel_root, active))
        return code, error

    _write(panel_root / "status.json", snapshot(plan, panel_root))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=int(plan.execution["concurrency"])
    ) as executor:
        futures = [executor.submit(run_one, job) for job in runnable]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    final = snapshot(plan, panel_root)
    _write(panel_root / "status.json", final)
    print(json.dumps(final["counts"], ensure_ascii=False))
    return 0 if final["counts"]["valid"] == len(jobs(plan)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
