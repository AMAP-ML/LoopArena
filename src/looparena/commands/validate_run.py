#!/usr/bin/env python3
"""Check the artifacts, arm boundary, budgets, and outcome of one harness run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from looparena.harness import validation as V
from looparena.harness.evaluator_protocol import (
    classify_infrastructure_validity,
    combine_final_infrastructure_validity,
    derive_final_task_outcome,
    derive_task_outcome,
    validate_evaluation_receipt,
    validate_evaluator_plan,
)
from looparena.harness.solve_preflight import validate_solve_environment_preflight


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}:must_contain_object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}:row_must_be_object")
        rows.append(value)
    return rows


def _artifact_path(
    run_dir: Path,
    manifest: dict[str, Any],
    key: str,
    *,
    directory: bool = False,
) -> Path:
    ref = (manifest.get("artifacts") or {}).get(key)
    if not isinstance(ref, str) or not ref:
        raise ValueError(f"artifact_ref_missing:{key}")
    path = (run_dir / ref).resolve()
    path.relative_to(run_dir)
    if directory and not path.is_dir():
        raise ValueError(f"artifact_directory_missing:{key}")
    if not directory and not path.is_file():
        raise ValueError(f"artifact_file_missing:{key}")
    return path


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _accounting_warnings(manifest: dict[str, Any]) -> list[str]:
    """Surface unavailable cost data without turning it into a task failure."""

    accounting = manifest.get("compute_accounting")
    if not isinstance(accounting, dict):
        return ["compute_accounting_missing"]
    warnings: list[str] = []
    main = accounting.get("main_worker")
    if not isinstance(main, dict):
        return ["compute_accounting_main_worker_missing"]
    if main.get("react_turns") != manifest.get("main_worker_turns"):
        warnings.append("compute_accounting_main_turns_differ")
    tokens = main.get("tokens")
    if not isinstance(tokens, dict):
        warnings.append("compute_accounting_main_tokens_missing")
    else:
        for field in ("request_count", "usage_reported_request_count"):
            if not _nonnegative_int(tokens.get(field)):
                warnings.append(f"compute_accounting_main_{field}_invalid")
        total = tokens.get("total_tokens")
        if total is not None and not _nonnegative_int(total):
            warnings.append("compute_accounting_main_total_tokens_invalid")
    return warnings


def validate_run_dir(
    run_dir: Path,
    *,
    require_terminal_evaluator: bool = False,
) -> dict[str, Any]:
    """Validate the minimum evidence needed for trustworthy aggregation."""

    run_dir = Path(run_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = _read_json(run_dir / "run_manifest.json")
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"manifest_invalid:{type(exc).__name__}:{exc}"],
            "warnings": [],
            "arm": None,
            "status": None,
            "infrastructure_valid": False,
            "task_success_at_budget": None,
        }

    arm = manifest.get("arm")
    if arm not in {"no-control", "controlled"}:
        errors.append("arm_invalid")
    if (
        not isinstance(manifest.get("sample_id"), str)
        or not manifest.get("sample_id", "").strip()
    ):
        errors.append("sample_id_invalid")
    if (
        not isinstance(manifest.get("status"), str)
        or not manifest.get("status", "").strip()
    ):
        errors.append("status_invalid")
    if not isinstance(manifest.get("termination_reason"), str):
        errors.append("termination_reason_invalid")

    budget = manifest.get("main_worker_turn_budget")
    turns = manifest.get("main_worker_turns")
    if not _nonnegative_int(budget) or budget == 0:
        errors.append("main_worker_turn_budget_invalid")
    if not _nonnegative_int(turns) or (_nonnegative_int(budget) and turns > budget):
        errors.append("main_worker_turns_invalid")

    try:
        start_context = _read_json(_artifact_path(run_dir, manifest, "start_context"))
        if start_context.get("sample_id") != manifest.get("sample_id"):
            errors.append("start_context_sample_id_mismatch")
        task = start_context.get("task")
        if not isinstance(task, str) or not task.strip():
            errors.append("start_context_task_invalid")
    except Exception as exc:
        start_context = {}
        errors.append(f"start_context_invalid:{type(exc).__name__}:{exc}")

    artifact_rows: dict[str, list[dict[str, Any]]] = {}
    for key in (
        "transcript",
        "main_worker_slices",
        "control_events",
        "reporter_runs",
        "packets",
        "controller_results",
        "controller_transcript",
    ):
        try:
            artifact_rows[key] = _read_jsonl(_artifact_path(run_dir, manifest, key))
        except Exception as exc:
            artifact_rows[key] = []
            errors.append(f"{key}_invalid:{type(exc).__name__}:{exc}")

    transcript = artifact_rows["transcript"]
    prefix_count = manifest.get("initial_prefix_message_count")
    if (
        not _nonnegative_int(prefix_count)
        or prefix_count == 0
        or prefix_count > len(transcript)
    ):
        errors.append("initial_prefix_message_count_invalid")
    else:
        errors.extend(V.validate_public_conversation(transcript[:prefix_count]))

    controller_transcript = artifact_rows["controller_transcript"]
    if controller_transcript:
        errors.extend(V.validate_public_conversation(controller_transcript))

    if arm == "no-control":
        for field in (
            "reporter_turns",
            "controller_calls",
            "report_count",
            "packet_count",
        ):
            if manifest.get(field) != 0:
                errors.append(f"no_control_{field}_must_be_zero")
        for key in ("reporter_runs", "packets", "controller_results"):
            if artifact_rows[key]:
                errors.append(f"no_control_{key}_must_be_empty")
        if (
            manifest.get("status") == "completed"
            and manifest.get("termination_reason") != "natural_completion"
        ):
            errors.append("no_control_completed_requires_natural_completion")
    elif arm == "controlled":
        counts = {
            "report_count": sum(
                isinstance(row.get("report"), dict)
                for row in artifact_rows["reporter_runs"]
            ),
            "packet_count": len(artifact_rows["packets"]),
            "controller_calls": len(artifact_rows["controller_results"]),
        }
        for field, observed in counts.items():
            if manifest.get(field) != observed:
                errors.append(f"controlled_{field}_artifact_count_mismatch")
        reporter_run_count = len(artifact_rows["reporter_runs"])
        reporter_terminal_failure = manifest.get("status") in {
            "reporter_budget_exhausted",
            "reporter_infrastructure_failure",
            "reporter_protocol_failure",
        }
        expected_reporter_runs = counts["report_count"] + (
            1 if reporter_terminal_failure else 0
        )
        if reporter_run_count != expected_reporter_runs:
            errors.append("controlled_reporter_artifact_count_mismatch")
        if manifest.get("status") == "completed":
            if manifest.get("termination_reason") != "controller_stop":
                errors.append("controlled_completed_requires_controller_stop")
            if counts["controller_calls"] < 1:
                errors.append("controlled_completed_requires_controller_call")
    preflight = manifest.get("solve_environment_preflight")
    if preflight is not None:
        errors.extend(validate_solve_environment_preflight(preflight))
    else:
        warnings.append("solve_environment_preflight_missing")

    warnings.extend(_accounting_warnings(manifest))

    solve_validity = classify_infrastructure_validity(
        manifest.get("status"), manifest.get("termination_reason")
    )
    evaluator = manifest.get("terminal_external_evaluator")
    if evaluator is None:
        if require_terminal_evaluator:
            errors.append("terminal_external_evaluator_required")
        final_validity = solve_validity
        outcome = derive_task_outcome(manifest, None)
    else:
        try:
            plan = _read_json(_artifact_path(run_dir, manifest, "evaluator_plan"))
            errors.extend(validate_evaluator_plan(plan))
            errors.extend(
                validate_evaluation_receipt(
                    evaluator,
                    plan=plan,
                )
            )
        except Exception as exc:
            errors.append(f"terminal_evaluator_invalid:{type(exc).__name__}:{exc}")
        final_validity = combine_final_infrastructure_validity(
            solve_validity, evaluator
        )
        outcome = derive_final_task_outcome(manifest, evaluator)

    # Stored classifications are caches. Differences are visible but never used
    # to overwrite the values recomputed above.
    if manifest.get("infrastructure_validity") not in (None, final_validity):
        warnings.append("stored_infrastructure_validity_differs")
    if manifest.get("task_outcome") not in (None, outcome):
        warnings.append("stored_task_outcome_differs")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "arm": arm,
        "status": manifest.get("status"),
        "infrastructure_valid": final_validity.get("valid") is True,
        "task_success_at_budget": outcome.get("success_at_budget"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--require-terminal-evaluator", action="store_true")
    args = parser.parse_args(argv)
    result = validate_run_dir(
        args.run_dir,
        require_terminal_evaluator=args.require_terminal_evaluator,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
