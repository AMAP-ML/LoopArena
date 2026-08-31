#!/usr/bin/env python3
"""Run one Type III case from its official problem origin.

The runner deliberately does not accept a saved workspace or serialized model
conversation as a task origin. SCBench executes every official checkpoint with
a persistent workspace and fresh model/controller context per checkpoint.
BeyondSWE executes one complete task from the pinned source-image parent commit.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from looparena.paths import repository_root

ROOT = repository_root()

from looparena.benchmarks.type3 import (
    BEYONDSWE_EVALUATOR_SCHEMA,
    CASE_SCHEMA,
    SEQUENCE_SCHEMA,
    read_object,
    safe_relative,
    sha256_file,
)
from looparena.benchmarks.type3_runtime import (
    _ensure_harbor_image,
    _ensure_scbench_image,
    _harbor_image_profile,
    _materialize_scbench_public_static_assets,
    _require_evaluator_assets,
    _scbench_runtime_profile,
    _solve_runtime,
    _source_solve_limits,
    localize_evaluator_plan,
)
from looparena.harness.recovery import atomic_write_json, read_checkpoint
from looparena.runtime.llm import DEFAULT_API_KEY_ENV, default_worker_base_url
from looparena.runtime.non_adaptive_fixed_controller import (
    POLICY_ID as FIXED_GOAL_POLICY_ID,
)
from looparena.runtime.sandbox import sanitize_workspace_git_history

PROVIDER_FAILURE_REASONS = {
    "main_worker_provider_failure",
    "reporter_provider_failure",
    "controller_provider_failure",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reported_token_total(usage: Any) -> int | None:
    """Return one component's complete model-token total, or fail closed."""

    if not isinstance(usage, dict):
        return None
    request_count = usage.get("request_count")
    reported_count = usage.get("usage_reported_request_count")
    total_tokens = usage.get("total_tokens")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (request_count, reported_count)
    ):
        return None
    if request_count == 0:
        return 0 if reported_count == 0 else None
    if reported_count != request_count:
        return None
    if (
        not isinstance(total_tokens, int)
        or isinstance(total_tokens, bool)
        or total_tokens < 0
    ):
        return None
    return total_tokens


def model_token_total_from_manifest(manifest: dict[str, Any]) -> int | None:
    """Sum complete Worker, Reporter, and Controller model-token usage."""

    accounting = manifest.get("compute_accounting")
    if not isinstance(accounting, dict):
        return None
    main_worker = accounting.get("main_worker")
    controlled_only = accounting.get("controlled_only")
    if not isinstance(main_worker, dict) or not isinstance(controlled_only, dict):
        return None
    components = (
        main_worker.get("tokens"),
        controlled_only.get("reporter_tokens"),
        controlled_only.get("controller_tokens"),
    )
    totals = [_reported_token_total(component) for component in components]
    if any(total is None for total in totals):
        return None
    return sum(int(total) for total in totals)


def aggregate_model_token_total(steps: list[dict[str, Any]]) -> int | None:
    """Sum checkpoint totals only when every executed step is report-complete."""

    total = 0
    for step in steps:
        value = step.get("total_tokens")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        total += value
    return total


def load_case(case_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_object(case_dir / "case.json")
    if manifest.get("schema_version") != CASE_SCHEMA:
        raise ValueError("unsupported_type3_case_schema")
    if manifest.get("case_id") != case_dir.name:
        raise ValueError("type3_case_id_directory_mismatch")
    if manifest.get("episode_origin") != "official_problem_start":
        raise ValueError("type3_case_is_not_official_start")
    if manifest.get("harness_start_mode") != "bootstrap_contract_start":
        raise ValueError("unsupported_type3_harness_start_mode")
    if not str(manifest.get("official_task_id") or "").strip():
        raise ValueError("type3_official_task_id_missing")
    sequence_path = safe_relative(
        case_dir, manifest.get("task_sequence_ref"), "task_sequence_ref"
    )
    sequence = read_object(sequence_path)
    if sequence.get("schema_version") != SEQUENCE_SCHEMA:
        raise ValueError("unsupported_type3_task_sequence_schema")
    if sequence.get("adapter_kind") != manifest.get("adapter_kind"):
        raise ValueError("type3_adapter_kind_mismatch")
    if sequence.get("official_task_id") != manifest.get("official_task_id"):
        raise ValueError("type3_official_task_id_mismatch")
    if sequence.get("task_scope") != manifest.get("task_scope"):
        raise ValueError("type3_task_scope_mismatch")
    steps = sequence.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("type3_task_sequence_is_empty")
    official_beyondswe: dict[str, Any] | None = None
    official_beyondswe_revision: str | None = None
    if manifest.get("adapter_kind") == "beyondswe_harbor":
        registry = read_object(
            case_dir.parents[1] / "BEYONDSWE_OFFICIAL_EVALUATORS.json"
        )
        if registry.get("schema_version") != BEYONDSWE_EVALUATOR_SCHEMA:
            raise ValueError("unsupported_beyondswe_official_evaluator_registry")
        if registry.get("formal_evaluator_policy") != "source-native-only":
            raise ValueError("beyondswe_evaluator_policy_is_not_source_native")
        official_beyondswe = (registry.get("cases") or {}).get(manifest["case_id"])
        if not isinstance(official_beyondswe, dict):
            raise ValueError("beyondswe_official_evaluator_binding_missing")
        official_beyondswe_revision = str(
            (registry.get("source") or {}).get("revision") or ""
        )
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("type3_task_step_is_not_object")
        task_path = safe_relative(case_dir, step.get("task_ref"), "task_ref")
        plan_path = safe_relative(
            case_dir, step.get("evaluator_plan_ref"), "evaluator_plan_ref"
        )
        # The checked-in task path is the package authority. The runtime hash
        # is derived here only to bind resumable attempts to these exact bytes;
        # it is not another release-manifest layer.
        step["task_sha256"] = sha256_file(task_path)
        if official_beyondswe is not None:
            plan = read_object(plan_path)
            if plan.get("evaluator_revision") is not None:
                raise ValueError("looparena_evaluator_revision_is_not_formal_type3")
            if plan.get("source_revision") != official_beyondswe_revision:
                raise ValueError("beyondswe_evaluator_source_revision_mismatch")
            if (plan.get("adapter_config") or {}).get(
                "task_dir"
            ) != official_beyondswe.get("asset_task_dir"):
                raise ValueError("beyondswe_evaluator_tree_binding_mismatch")
    if manifest.get("adapter_kind") == "scbench":
        names = [str(step.get("step_id") or "") for step in steps]
        expected = [f"checkpoint_{index}" for index in range(1, len(steps) + 1)]
        if names != expected or sequence.get("target_step") != names[-1]:
            raise ValueError("type3_incomplete_official_checkpoint_sequence")
        if sequence.get("official_checkpoint_count") != len(steps):
            raise ValueError("type3_official_checkpoint_count_mismatch")
        if sequence.get("checkpoint_coverage_policy") != ("all_official_checkpoints"):
            raise ValueError("type3_checkpoint_coverage_policy_mismatch")
        if sequence.get("checkpoint_failure_policy") != (
            "continue_after_valid_task_failure_stop_on_infrastructure_invalid"
        ):
            raise ValueError("type3_checkpoint_failure_policy_mismatch")
        if sequence.get("aggregate_pass_policy") != ("all_official_checkpoints_pass"):
            raise ValueError("type3_aggregate_pass_policy_mismatch")
    elif len(steps) != 1 or steps[0].get("step_id") != "task":
        raise ValueError("type3_beyondswe_must_be_one_complete_task")
    return manifest, sequence


def prepare_plan(
    *,
    stored_plan: dict[str, Any],
    case_dir: Path,
    assets_root: Path,
    scbench_runtime_profile: str,
) -> tuple[dict[str, Any], str]:
    adapter = stored_plan.get("adapter_kind")
    if adapter == "scbench":
        profile = _scbench_runtime_profile(
            stored_plan, case_dir, scbench_runtime_profile
        )
        resolved_image_id = _ensure_scbench_image(stored_plan, assets_root, profile)
    elif adapter == "beyondswe_harbor":
        if scbench_runtime_profile != "canonical-amd64":
            raise ValueError("--scbench-runtime-profile applies only to SCBench cases")
        profile = _harbor_image_profile(stored_plan, case_dir)
        resolved_image_id = _ensure_harbor_image(stored_plan, assets_root, profile)
    else:
        raise ValueError(f"unsupported evaluator adapter: {adapter}")
    localized = localize_evaluator_plan(stored_plan, assets_root=assets_root)
    runtime = localized["runtime_identity"]
    if adapter == "scbench":
        runtime["evaluation_image_id"] = resolved_image_id
    _require_evaluator_assets(localized)
    return localized, resolved_image_id


def run_checked(argv: list[str], *, error: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{error}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def verified_beyondswe_submodules(workspace: Path) -> list[dict[str, str]]:
    """Require every initialized submodule to match its pinned gitlink.

    BeyondSWE source images are the immutable official-start authority.  Some
    legitimate images, notably aiohttp for case039, include initialized
    submodules.  We keep that official source content, but never fetch or track
    a moving remote: every checked-out submodule must already match the commit
    recorded by the superproject before its Git metadata is sanitized.
    """

    expected = run_checked(
        [
            "git",
            "-C",
            str(workspace),
            "submodule",
            "status",
            "--cached",
            "--recursive",
        ],
        error="failed to read pinned BeyondSWE submodule commits",
    ).stdout.splitlines()
    observed = run_checked(
        ["git", "-C", str(workspace), "submodule", "status", "--recursive"],
        error="failed to read BeyondSWE submodule worktrees",
    ).stdout.splitlines()

    def identities(
        lines: list[str],
        *,
        require_initialized: bool,
    ) -> list[dict[str, str]]:
        identities: list[dict[str, str]] = []
        for line in lines:
            if len(line) < 42 or line[0] not in {" ", "-", "+", "U"}:
                raise RuntimeError("unparseable BeyondSWE submodule status")
            if require_initialized and line[0] != " ":
                raise RuntimeError(
                    "BeyondSWE official submodule is not initialized at its gitlink"
                )
            fields = line[1:].split(maxsplit=1)
            if len(fields) != 2 or len(fields[0]) not in {40, 64}:
                raise RuntimeError("unparseable BeyondSWE submodule identity")
            path = fields[1].rsplit(" (", 1)[0]
            identities.append({"path": path, "commit": fields[0].lower()})
        return identities

    expected_identities = identities(expected, require_initialized=False)
    observed_identities = identities(observed, require_initialized=True)
    if observed_identities != expected_identities:
        raise RuntimeError("BeyondSWE official submodule gitlink mismatch")
    return observed_identities


def require_clean_beyondswe_worktree(workspace: Path) -> None:
    """Reject dirty source-image state, including dirty submodule worktrees."""

    status = run_checked(
        [
            "git",
            "-C",
            str(workspace),
            "status",
            "--porcelain=v1",
            "--ignore-submodules=none",
        ],
        error="failed to inspect BeyondSWE official worktree",
    ).stdout
    if status.strip():
        raise RuntimeError("BeyondSWE official origin worktree is not clean")


def materialize_beyondswe_origin(
    *,
    plan: dict[str, Any],
    sequence: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    config = plan.get("adapter_config") or {}
    runtime = plan.get("runtime_identity") or {}
    docker = str(config.get("docker_executable") or "docker")
    image = str(runtime.get("image_id") or runtime.get("image_reference") or "")
    platform = str(runtime.get("platform") or "linux/amd64")
    source_workdir = str(sequence.get("official_workdir") or "")
    parent_commit = str(sequence.get("official_parent_commit") or "")
    if not image or not source_workdir.startswith("/") or not parent_commit:
        raise ValueError("incomplete BeyondSWE official origin")
    created = run_checked(
        [
            docker,
            "create",
            "--platform",
            platform,
            "--entrypoint",
            "sleep",
            image,
            "infinity",
        ],
        error="failed to open BeyondSWE source image",
    )
    container_id = created.stdout.strip().splitlines()[-1]
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        run_checked(
            [docker, "cp", f"{container_id}:{source_workdir}/.", str(workspace)],
            error="failed to copy BeyondSWE official repository",
        )
    finally:
        subprocess.run(
            [docker, "rm", "-f", container_id],
            capture_output=True,
            text=True,
            check=False,
        )
    run_checked(
        ["git", "-C", str(workspace), "reset", "--hard", parent_commit],
        error="official parent commit is unavailable in source image",
    )
    run_checked(
        ["git", "-C", str(workspace), "clean", "-fdx"],
        error="failed to clean official BeyondSWE repository",
    )
    observed = run_checked(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        error="failed to read official BeyondSWE repository HEAD",
    ).stdout.strip()
    if observed != parent_commit:
        raise RuntimeError("BeyondSWE official origin HEAD mismatch")
    require_clean_beyondswe_worktree(workspace)
    submodules = verified_beyondswe_submodules(workspace)
    sanitize_workspace_git_history(workspace)
    if verified_beyondswe_submodules(workspace) != submodules:
        raise RuntimeError("BeyondSWE submodule identity changed during sanitization")
    require_clean_beyondswe_worktree(workspace)
    return {
        "workspace_origin": "pinned_source_image_parent_commit",
        "official_parent_commit": parent_commit,
        "observed_parent_commit": observed,
        "official_workdir": source_workdir,
        "resolved_image_id": image,
        "verified_submodules": submodules,
    }


def materialize_official_origin(
    *,
    manifest: dict[str, Any],
    sequence: dict[str, Any],
    first_plan: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    if manifest["adapter_kind"] == "scbench":
        workspace.mkdir(parents=True, exist_ok=False)
        _materialize_scbench_public_static_assets(first_plan, workspace)
        config = first_plan.get("adapter_config") or {}
        return {
            "workspace_origin": "empty_workspace_plus_declared_public_static_assets",
            "problem_name": config.get("problem_name"),
            "private_bundle_path_exposed_to_model": False,
        }
    return materialize_beyondswe_origin(
        plan=first_plan,
        sequence=sequence,
        workspace=workspace,
    )


def final_step_state(manifest: dict[str, Any]) -> tuple[str, bool | None]:
    validity = manifest.get("infrastructure_validity") or {}
    if (
        validity.get("valid") is not True
        or manifest.get("evaluation_state") != "completed"
    ):
        return "infrastructure_invalid", None
    outcome = manifest.get("task_outcome") or {}
    success = outcome.get("success_at_budget")
    if success is True:
        return "passed", True
    if success is False:
        return "failed", False
    return "infrastructure_invalid", None


def continue_after_step(sequence: dict[str, Any], step_state: str) -> bool:
    """Return whether the next native official step may execute."""

    if step_state == "infrastructure_invalid":
        return False
    if step_state == "failed":
        return sequence.get("checkpoint_failure_policy") == (
            "continue_after_valid_task_failure_stop_on_infrastructure_invalid"
        )
    return step_state == "passed"


def audited_completed_steps(
    *,
    out_dir: Path,
    aggregate: dict[str, Any],
    manifest: dict[str, Any],
    sequence: dict[str, Any],
    arm: str,
    seed: int,
) -> list[dict[str, Any]]:
    """Validate completed checkpoints before continuing a Type III task.

    Existing checkpoint attempts are reusable only when they are the exact
    initial segment of the official checkpoint order, every child manifest is
    infrastructure-valid, and every frozen task hash matches this package.
    Valid model work is never replayed.
    """

    if aggregate.get("case_id") != manifest["case_id"]:
        raise ValueError("resume_case_id_mismatch")
    if aggregate.get("arm") != arm or aggregate.get("seed") != seed:
        raise ValueError("resume_arm_or_seed_mismatch")
    if aggregate.get("episode_origin") != "official_problem_start":
        raise ValueError("resume_is_not_official_origin")
    if not (out_dir / "workspace").is_dir():
        raise ValueError("resume_workspace_missing")
    existing = aggregate.get("steps")
    if not isinstance(existing, list) or not existing:
        raise ValueError("resume_completed_checkpoints_missing")
    official_steps = sequence["steps"]
    existing_names = [str(step.get("step_id") or "") for step in existing]
    expected_names = [str(step["step_id"]) for step in official_steps[: len(existing)]]
    if existing_names != expected_names or len(existing) >= len(official_steps):
        raise ValueError("resume_is_not_an_incomplete_official_sequence")
    audited: list[dict[str, Any]] = []
    for record, official_step in zip(existing, official_steps[: len(existing)]):
        step_id = str(record["step_id"])
        child_path = out_dir / "checkpoints" / step_id / "run_manifest.json"
        if not child_path.is_file():
            raise ValueError(f"resume_child_manifest_missing:{step_id}")
        child = read_object(child_path)
        state, task_passed = final_step_state(child)
        if state not in {"passed", "failed"}:
            raise ValueError(f"resume_child_is_infrastructure_invalid:{step_id}")
        if record.get("status") != state or record.get("task_passed") != task_passed:
            raise ValueError(f"resume_child_state_mismatch:{step_id}")
        if child.get("task_sha256") != official_step.get("task_sha256"):
            raise ValueError(f"resume_task_hash_mismatch:{step_id}")
        audited_record = dict(record)
        audited_record["total_tokens"] = model_token_total_from_manifest(child)
        audited.append(audited_record)
    return audited


def audited_provider_interrupted_step(
    *,
    out_dir: Path,
    aggregate: dict[str, Any],
    manifest: dict[str, Any],
    sequence: dict[str, Any],
    arm: str,
    seed: int,
    worker_model: str,
    controller_provider: str,
    controller_model: str | None,
) -> tuple[list[dict[str, Any]], str] | None:
    """Audit a trailing provider-interrupted checkpoint for durable resume."""

    existing = aggregate.get("steps")
    if not isinstance(existing, list) or not existing:
        return None
    trailing = existing[-1]
    step_id = str(trailing.get("step_id") or "")
    step_out = out_dir / "checkpoints" / step_id
    child_path = step_out / "run_manifest.json"
    checkpoint_path = step_out / "recovery_checkpoint.json"
    if not child_path.is_file() or not checkpoint_path.is_file():
        return None
    child = read_object(child_path)
    if child.get("termination_reason") not in PROVIDER_FAILURE_REASONS:
        return None
    validity = child.get("infrastructure_validity") or {}
    if validity.get("valid") is not False:
        raise ValueError(f"resume_provider_failure_validity_mismatch:{step_id}")
    if trailing.get("status") != "infrastructure_invalid":
        raise ValueError(f"resume_provider_failure_aggregate_mismatch:{step_id}")

    if aggregate.get("case_id") != manifest["case_id"]:
        raise ValueError("resume_case_id_mismatch")
    if aggregate.get("arm") != arm or aggregate.get("seed") != seed:
        raise ValueError("resume_arm_or_seed_mismatch")
    if aggregate.get("episode_origin") != "official_problem_start":
        raise ValueError("resume_is_not_official_origin")
    if not (out_dir / "workspace").is_dir():
        raise ValueError("resume_workspace_missing")

    official_steps = sequence["steps"]
    if len(existing) > len(official_steps):
        raise ValueError("resume_has_too_many_checkpoint_records")
    existing_names = [str(step.get("step_id") or "") for step in existing]
    expected_names = [str(step["step_id"]) for step in official_steps[: len(existing)]]
    if existing_names != expected_names:
        raise ValueError("resume_is_not_an_official_checkpoint_prefix")

    completed = existing[:-1]
    if completed:
        completed_aggregate = {**aggregate, "steps": completed}
        completed = audited_completed_steps(
            out_dir=out_dir,
            aggregate=completed_aggregate,
            manifest=manifest,
            sequence=sequence,
            arm=arm,
            seed=seed,
        )

    official_step = official_steps[len(completed)]
    checkpoint = read_checkpoint(checkpoint_path)
    expected_sample_id = f"{manifest['official_task_id']}:{step_id}"
    expected_provider = controller_provider if arm == "controlled" else ""
    runtime = checkpoint.get("runtime_identity") or {}
    worker = runtime.get("worker") or {}
    controller = runtime.get("controller") or {}
    for key, expected in (
        ("arm", arm),
        ("sample_id", expected_sample_id),
        ("seed", seed),
        ("start_mode", "bootstrap_contract_start"),
        ("task_sha256", official_step.get("task_sha256")),
    ):
        if checkpoint.get(key) != expected:
            raise ValueError(f"resume_checkpoint_identity_mismatch:{key}")
    if checkpoint.get("safe_to_resume") is not True:
        raise ValueError("resume_checkpoint_is_not_safe")
    if worker.get("model") != worker_model:
        raise ValueError("resume_worker_model_mismatch")
    if str(controller.get("provider_kind") or "") != expected_provider:
        raise ValueError("resume_controller_provider_mismatch")
    if controller_model is not None and controller.get("model") != controller_model:
        raise ValueError("resume_controller_model_mismatch")
    return completed, step_id


def prepare_provider_interrupted_step_resume(
    *,
    out_dir: Path,
    step_id: str,
) -> Path:
    """Archive an immutable failed attempt and stage its safe checkpoint."""

    step_out = out_dir / "checkpoints" / step_id
    checkpoint = read_checkpoint(step_out / "recovery_checkpoint.json")
    attempt_index = 1
    while True:
        archived = step_out.with_name(
            f"{step_id}.provider-failure-attempt-{attempt_index}"
        )
        if not archived.exists():
            break
        attempt_index += 1
    step_out.rename(archived)
    step_out.mkdir()
    shutil.copyfile(
        archived / "recovery_checkpoint.json",
        step_out / "recovery_checkpoint.json",
    )
    atomic_write_json(
        step_out / "resumed_from.json",
        {
            "source_attempt": archived.name,
            "source_worker_turns": int(checkpoint.get("main_worker_turns") or 0),
            "source_cycle_index": int(checkpoint.get("cycle_index") or 0),
            "resume_boundary": str(checkpoint.get("phase") or "call_worker"),
        },
    )
    return archived


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=("controlled", "no-control"))
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--assets-root", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument(
        "--materialize-origin-only",
        action="store_true",
        help="Create and audit the official workspace origin without model calls.",
    )
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Continue an incomplete official checkpoint sequence in the existing "
            "--out-dir workspace; completed checkpoints are audited and never rerun, "
            "and a provider-interrupted checkpoint resumes from its safe boundary."
        ),
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--worker-model", default="qwen3.7-plus")
    parser.add_argument("--controller-model", default="qwen3.7-plus")
    parser.add_argument(
        "--controller-provider",
        choices=("model", "non-adaptive-fixed"),
        default="model",
        help=(
            "Use a model Controller or the deterministic non-adaptive "
            "fixed-control baseline."
        ),
    )
    parser.add_argument(
        "--scbench-runtime-profile",
        choices=("canonical-amd64",),
        default="canonical-amd64",
        help="Official SCBench linux/amd64 runtime.",
    )
    parser.add_argument("--base-url", default=default_worker_base_url())
    parser.add_argument(
        "--controller-base-url",
        default=os.environ.get("LOOPARENA_CONTROLLER_BASE_URL", ""),
    )
    parser.add_argument("--credential-profile-id", default="default-gateway")
    parser.add_argument("--controller-credential-profile-id")
    parser.add_argument("--controller-api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--worker-wall-time-sec", type=int, default=7200)
    parser.add_argument("--reporter-wall-time-sec", type=int, default=900)
    parser.add_argument("--gateway-timeout-sec", type=int, default=900)
    parser.add_argument("--tool-timeout-sec", type=int, default=120)
    parser.add_argument("harness_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.controller_provider != "model" and args.arm != "controlled":
        raise SystemExit("non-model controller providers are controlled-only")
    case_dir = args.case_dir.expanduser().resolve()
    assets_root = args.assets_root.expanduser().resolve()
    manifest, sequence = load_case(case_dir)
    if (
        sum(
            bool(value)
            for value in (
                args.preflight_only,
                args.materialize_origin_only,
                args.resume_existing,
            )
        )
        > 1
    ):
        raise SystemExit(
            "--preflight-only, --materialize-origin-only, and --resume-existing "
            "are mutually exclusive"
        )
    prepared_steps: list[tuple[dict[str, Any], Path, dict[str, Any], str]] = []
    for step in sequence["steps"]:
        task_path = safe_relative(case_dir, step["task_ref"], "task_ref")
        plan_path = safe_relative(
            case_dir, step["evaluator_plan_ref"], "evaluator_plan_ref"
        )
        plan, image_id = prepare_plan(
            stored_plan=read_object(plan_path),
            case_dir=case_dir,
            assets_root=assets_root,
            scbench_runtime_profile=args.scbench_runtime_profile,
        )
        prepared_steps.append((step, task_path, plan, image_id))
    if args.preflight_only:
        print(
            json.dumps(
                {
                    "case_id": manifest["case_id"],
                    "adapter_kind": manifest["adapter_kind"],
                    "episode_origin": manifest["episode_origin"],
                    "task_steps": len(prepared_steps),
                    "resolved_image_ids": sorted({item[3] for item in prepared_steps}),
                    "model_calls": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.out_dir is None:
        raise SystemExit("--out-dir is required unless --preflight-only is used")
    out_dir = args.out_dir.expanduser().resolve()
    if args.resume_existing:
        if not out_dir.is_dir():
            raise SystemExit(f"resume out dir does not exist: {out_dir}")
        aggregate = read_object(out_dir / "run_manifest.json")
        provider_interrupted = audited_provider_interrupted_step(
            out_dir=out_dir,
            aggregate=aggregate,
            manifest=manifest,
            sequence=sequence,
            arm=args.arm,
            seed=args.seed,
            worker_model=args.worker_model,
            controller_provider=(
                args.controller_provider if args.arm == "controlled" else ""
            ),
            controller_model=(
                FIXED_GOAL_POLICY_ID
                if args.controller_provider == "non-adaptive-fixed"
                else args.controller_model
                if args.arm == "controlled"
                else None
            ),
        )
        if provider_interrupted is None:
            existing_steps = audited_completed_steps(
                out_dir=out_dir,
                aggregate=aggregate,
                manifest=manifest,
                sequence=sequence,
                arm=args.arm,
                seed=args.seed,
            )
            interrupted_step_id = None
        else:
            existing_steps, interrupted_step_id = provider_interrupted
            prepare_provider_interrupted_step_resume(
                out_dir=out_dir,
                step_id=interrupted_step_id,
            )
        aggregate["steps"] = list(existing_steps)
        workspace = out_dir / "workspace"
        aggregate.update(
            {
                "official_task_id": manifest["official_task_id"],
                "task_scope": manifest["task_scope"],
                "checkpoint_context_policy": sequence["checkpoint_context_policy"],
                "checkpoint_failure_policy": sequence["checkpoint_failure_policy"],
                "aggregate_pass_policy": sequence.get("aggregate_pass_policy"),
                "target_step": sequence["target_step"],
                "status": "running",
                "task_passed": None,
                "finished_at": None,
                "executed_steps": len(existing_steps),
                "passed_steps": sum(
                    step["status"] == "passed" for step in existing_steps
                ),
                "failed_steps": sum(
                    step["status"] == "failed" for step in existing_steps
                ),
                "all_official_steps_executed": False,
                "total_main_worker_turns": sum(
                    int(step.get("main_worker_turns") or 0) for step in existing_steps
                ),
                "total_tokens": aggregate_model_token_total(existing_steps),
            }
        )
        start_index = len(existing_steps)
    else:
        if out_dir.exists():
            raise SystemExit(f"out dir already exists: {out_dir}")
        out_dir.mkdir(parents=True)
        workspace = out_dir / "workspace"
        origin = materialize_official_origin(
            manifest=manifest,
            sequence=sequence,
            first_plan=prepared_steps[0][2],
            workspace=workspace,
        )
        atomic_write_json(out_dir / "official_origin_manifest.json", origin)
        start_index = 0
    if args.materialize_origin_only:
        atomic_write_json(
            out_dir / "run_manifest.json",
            {
                "case_id": manifest["case_id"],
                "episode_origin": "official_problem_start",
                "status": "origin_materialized",
                "model_calls": 0,
                "official_origin_manifest": "official_origin_manifest.json",
            },
        )
        print(
            json.dumps(
                {
                    "case_id": manifest["case_id"],
                    "status": "origin_materialized",
                    "model_calls": 0,
                },
                sort_keys=True,
            )
        )
        return 0
    if not args.resume_existing:
        aggregate = {
            "case_id": manifest["case_id"],
            "official_task_id": manifest["official_task_id"],
            "task_scope": manifest["task_scope"],
            "arm": args.arm,
            "seed": args.seed,
            "episode_origin": "official_problem_start",
            "checkpoint_context_policy": sequence["checkpoint_context_policy"],
            "checkpoint_failure_policy": sequence["checkpoint_failure_policy"],
            "aggregate_pass_policy": sequence.get("aggregate_pass_policy"),
            "started_at": now(),
            "status": "running",
            "task_passed": None,
            "steps": [],
        }
    atomic_write_json(out_dir / "run_manifest.json", aggregate)
    for step, task_path, evaluator_plan, _ in prepared_steps[start_index:]:
        step_id = str(step["step_id"])
        step_out = out_dir / "checkpoints" / step_id
        step_out.parent.mkdir(parents=True, exist_ok=True)
        (
            solve_image,
            mount_point,
            solve_network,
            solve_setup_commands,
        ) = _solve_runtime(evaluator_plan)
        solve_cpus, solve_memory_mb = _source_solve_limits(evaluator_plan)
        with tempfile.TemporaryDirectory(
            prefix=f"{manifest['case_id']}-{step_id}-evaluator-",
            dir=out_dir,
        ) as temporary:
            localized_plan = Path(temporary) / "evaluator_plan.json"
            localized_plan.write_text(
                json.dumps(evaluator_plan, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            command = [
                sys.executable,
                "-m",
                "looparena.commands.harness",
                "--arm",
                args.arm,
                "--task-file",
                str(task_path),
                "--workspace",
                str(workspace),
                "--out-dir",
                str(step_out),
                "--sample-id",
                f"{manifest['official_task_id']}:{step_id}",
                "--seed",
                str(args.seed),
                "--worker-model",
                args.worker_model,
                "--controller-model",
                args.controller_model,
                "--credential-profile-id",
                args.credential_profile_id,
                "--controller-api-key-env",
                args.controller_api_key_env,
                "--image",
                solve_image,
                "--mount-point",
                mount_point,
                "--network",
                solve_network,
                "--worker-wall-time-sec",
                str(args.worker_wall_time_sec),
                "--reporter-wall-time-sec",
                str(args.reporter_wall_time_sec),
                "--gateway-timeout-sec",
                str(args.gateway_timeout_sec),
                "--tool-timeout-sec",
                str(args.tool_timeout_sec),
                "--evaluator-plan",
                str(localized_plan),
            ]
            if solve_cpus is not None:
                command.extend(["--cpus", str(solve_cpus)])
            if solve_memory_mb is not None:
                command.extend(["--memory-mb", str(solve_memory_mb)])
            if solve_setup_commands:
                if evaluator_plan.get("adapter_kind") == "scbench":
                    command.extend(["--solve-setup-timeout-sec", "310"])
                for setup_command in solve_setup_commands:
                    command.extend(["--solve-setup-command", setup_command])
            if args.base_url:
                command.extend(["--base-url", args.base_url])
            if args.controller_base_url:
                command.extend(["--controller-base-url", args.controller_base_url])
            if args.controller_credential_profile_id:
                command.extend(
                    [
                        "--controller-credential-profile-id",
                        args.controller_credential_profile_id,
                    ]
                )
            if args.controller_provider != "model":
                command.extend(["--controller-provider", args.controller_provider])
            if args.resume_existing and step_id == interrupted_step_id:
                command.append("--resume-run")
            command.extend(item for item in args.harness_args if item != "--")
            environment = os.environ.copy()
            if evaluator_plan.get("adapter_kind") == "scbench":
                platform_name = str(
                    (evaluator_plan.get("runtime_identity") or {}).get(
                        "evaluation_image_platform"
                    )
                    or "linux/amd64"
                ).replace("/", "-")
                cache = (
                    assets_root
                    / "scbench_runtime_cache"
                    / f"{platform_name}-py312"
                    / "uv-cache"
                )
                cache.mkdir(parents=True, exist_ok=True)
                environment["LOOPARENA_SCBENCH_UV_CACHE_DIR"] = str(cache)
            return_code = subprocess.run(
                command, cwd=ROOT, env=environment, check=False
            ).returncode
        step_manifest_path = step_out / "run_manifest.json"
        if not step_manifest_path.is_file():
            step_state, task_passed = "infrastructure_invalid", None
            step_manifest = {}
        else:
            step_manifest = read_object(step_manifest_path)
            step_state, task_passed = final_step_state(step_manifest)
        aggregate["steps"].append(
            {
                "step_id": step_id,
                "status": step_state,
                "task_passed": task_passed,
                "return_code": return_code,
                "run_manifest_ref": f"checkpoints/{step_id}/run_manifest.json",
                "main_worker_turns": step_manifest.get("main_worker_turns"),
                "total_tokens": model_token_total_from_manifest(step_manifest),
            }
        )
        aggregate["total_tokens"] = aggregate_model_token_total(aggregate["steps"])
        atomic_write_json(out_dir / "run_manifest.json", aggregate)
        if step_state == "infrastructure_invalid":
            aggregate["status"] = "infrastructure_invalid"
            aggregate["task_passed"] = None
            break
        if not continue_after_step(sequence, step_state):
            aggregate["status"] = "failed"
            aggregate["task_passed"] = False
            break
    else:
        aggregate["task_passed"] = all(
            step["status"] == "passed" for step in aggregate["steps"]
        )
        aggregate["status"] = "completed" if aggregate["task_passed"] else "failed"
    aggregate["finished_at"] = now()
    aggregate["executed_steps"] = len(aggregate["steps"])
    aggregate["passed_steps"] = sum(
        step["status"] == "passed" for step in aggregate["steps"]
    )
    aggregate["failed_steps"] = sum(
        step["status"] == "failed" for step in aggregate["steps"]
    )
    aggregate["all_official_steps_executed"] = len(aggregate["steps"]) == len(
        sequence["steps"]
    )
    aggregate["target_step"] = sequence["target_step"]
    aggregate["total_main_worker_turns"] = sum(
        int(step.get("main_worker_turns") or 0) for step in aggregate["steps"]
    )
    aggregate["total_tokens"] = aggregate_model_token_total(aggregate["steps"])
    atomic_write_json(out_dir / "run_manifest.json", aggregate)
    return 0 if aggregate["status"] in {"completed", "failed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
