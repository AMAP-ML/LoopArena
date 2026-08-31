#!/usr/bin/env python3
"""Summarize one generic Type II panel without scoring infrastructure gaps as zero."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
from pathlib import Path
from typing import Any, Iterable

from looparena.commands.type2_panel import (
    PanelPlan,
    attempt_dirs,
    job_root,
    jobs_for_plan,
    latest_valid,
    load_resolved_plan,
    panel_writer_active,
    read_object,
    safe_resume_source,
)
from looparena.harness.evaluator_protocol import derive_protocol_status


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 4) if denominator else None


def _mean(values: Iterable[int | float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return round(statistics.fmean(present), 4) if present else None


def _reported_token_total(usage: object) -> int | None:
    if not isinstance(usage, dict):
        return None
    request_count = _int(usage.get("request_count"))
    reported_count = _int(usage.get("usage_reported_request_count"))
    if request_count is None or reported_count is None:
        return None
    if request_count == 0:
        return 0 if reported_count == 0 else None
    if reported_count != request_count:
        return None
    total = _int(usage.get("total_tokens"))
    return total if total is not None and total >= 0 else None


def _manifest_usage(attempt: Path) -> dict[str, Any]:
    manifest = read_object(attempt / "run_manifest.json")
    accounting = manifest.get("compute_accounting") or {}
    main = accounting.get("main_worker") or {}
    controlled = accounting.get("controlled_only") or {}
    token_groups = [
        main.get("tokens") or {},
        controlled.get("reporter_tokens") or {},
        controlled.get("controller_tokens") or {},
    ]
    reported_tokens = [_reported_token_total(group) for group in token_groups]
    expected_components = 3 if manifest.get("arm") == "controlled" else 1
    reported_components = sum(value is not None for value in reported_tokens)
    total_tokens = (
        sum(int(value) for value in reported_tokens if value is not None)
        if reported_components == expected_components
        else None
    )
    return {
        "worker_turns": _int(manifest.get("main_worker_turns")),
        "reporter_turns": _int(manifest.get("reporter_turns")),
        "controller_calls": _int(manifest.get("controller_calls")),
        "protocol_valid": derive_protocol_status(manifest)["protocol_valid"],
        "reported_total_tokens": total_tokens,
        "token_components_reported": reported_components,
    }


def _attempt_process_running(attempt: Path) -> bool:
    try:
        state = read_object(attempt / "attempt_state.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    pid = state.get("pid")
    if state.get("status") != "running" or not isinstance(pid, int) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def collect_records(panel_root: Path, plan: PanelPlan) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        panel_status = read_object(panel_root / "status.json")
    except (OSError, ValueError, json.JSONDecodeError):
        panel_status = {}
    status_records = panel_status.get("records") or {}
    writer_active = panel_writer_active(panel_root)
    for job in jobs_for_plan(plan):
        root = job_root(panel_root, job)
        attempts = attempt_dirs(root)
        scheduler_state = status_records.get(job.key) or {}
        valid = latest_valid(root, plan, job)
        row: dict[str, Any] = {
            "case_id": job.case_id,
            "selection_stage": plan.selection_stage.get(job.case_id, "unstratified"),
            "condition_id": job.condition_id,
            "arm": plan.condition(job.condition_id).arm,
            "seed": job.seed,
            "state": "pending",
            "task_passed": None,
            "attempt": None,
            "attempt_count": len(attempts),
        }
        if valid is not None:
            attempt = Path(str(valid["attempt"]))
            row.update(
                {
                    "state": "valid",
                    "task_passed": valid["task_passed"],
                    "attempt": str(attempt),
                    **_manifest_usage(attempt),
                }
            )
        elif (
            writer_active
            and scheduler_state.get("status")
            in {"running", "retrying", "waiting_for_docker"}
        ) or (attempts and _attempt_process_running(attempts[-1])):
            row["state"] = "running"
            row["attempt"] = str(attempts[-1]) if attempts else None
        elif any(
            safe_resume_source(attempt, plan, job) for attempt in reversed(attempts)
        ):
            row["state"] = "interrupted_resumable"
            row["attempt"] = str(attempts[-1])
        elif row["attempt_count"] or scheduler_state.get("status") in {
            "infrastructure_unresolved",
            "orchestration_error",
            "preflight_blocked",
        }:
            row["state"] = "infrastructure_invalid"
        records.append(row)
    return records


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if row["state"] == "valid"]
    passed = [row for row in valid if row["task_passed"] is True]
    failed = [row for row in valid if row["task_passed"] is False]
    infrastructure = [row for row in rows if row["state"] == "infrastructure_invalid"]
    running = [row for row in rows if row["state"] == "running"]
    interrupted_resumable = [
        row for row in rows if row["state"] == "interrupted_resumable"
    ]
    pending = [row for row in rows if row["state"] == "pending"]
    complete = len(valid) == len(rows)
    token_values = [row.get("reported_total_tokens") for row in valid]
    token_complete = complete and all(value is not None for value in token_values)
    return {
        "planned": len(rows),
        "valid": len(valid),
        "passed": len(passed),
        "failed": len(failed),
        "infrastructure_invalid": len(infrastructure),
        "running": len(running),
        "interrupted_resumable": len(interrupted_resumable),
        "pending": len(pending),
        "complete": complete,
        # The formal score is deliberately unavailable until every planned
        # slot has a valid model outcome. Infrastructure gaps are not zeroes.
        "strict_success_rate_pct": _rate(len(passed), len(rows)) if complete else None,
        "observed_valid_success_rate_pct": _rate(len(passed), len(valid)),
        "protocol_failure_rate_pct": _rate(
            sum(row.get("protocol_valid") is False for row in valid),
            len(valid),
        ),
        "mean_worker_turns": _mean(row.get("worker_turns") for row in valid),
        "mean_reporter_turns": _mean(row.get("reporter_turns") for row in valid),
        "mean_controller_calls": _mean(row.get("controller_calls") for row in valid),
        "reported_total_tokens": (
            sum(int(value) for value in token_values if value is not None)
            if token_complete
            else None
        ),
        "token_reported_runs": sum(
            row.get("reported_total_tokens") is not None for row in valid
        ),
    }


def summarize(panel_root: Path) -> dict[str, Any]:
    panel_root = panel_root.expanduser().resolve()
    plan = load_resolved_plan(panel_root / "resolved_plan.json")
    records = collect_records(panel_root, plan)
    condition_summaries: dict[str, dict[str, Any]] = {}
    selection_stages = sorted(set(plan.selection_stage.values()))
    for condition in plan.conditions:
        rows = [row for row in records if row["condition_id"] == condition.condition_id]
        summary = _group_summary(rows)
        summary["arm"] = condition.arm
        summary["controller"] = (
            {
                "provider": condition.provider,
                "model": condition.model,
                "transport": condition.transport,
            }
            if condition.arm == "controlled"
            else None
        )
        summary["by_selection_stage"] = {
            stage: _group_summary(
                [row for row in rows if row["selection_stage"] == stage]
            )
            for stage in selection_stages
        }
        condition_summaries[condition.condition_id] = summary

    by_slot: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in records:
        by_slot.setdefault((row["case_id"], row["seed"]), {})[row["condition_id"]] = row
    condition_ids = [condition.condition_id for condition in plan.conditions]
    common_slots = [
        slot
        for slot, values in by_slot.items()
        if all(
            values[condition_id]["state"] == "valid" for condition_id in condition_ids
        )
    ]
    common_summary = {
        "slot_count": len(common_slots),
        "case_seed_slots": [
            {"case_id": case_id, "seed": seed} for case_id, seed in common_slots
        ],
        "conditions": {
            condition_id: {
                "passed": sum(
                    by_slot[slot][condition_id]["task_passed"] is True
                    for slot in common_slots
                ),
                "success_rate_pct": _rate(
                    sum(
                        by_slot[slot][condition_id]["task_passed"] is True
                        for slot in common_slots
                    ),
                    len(common_slots),
                ),
            }
            for condition_id in condition_ids
        },
    }

    pairwise: dict[str, Any] = {}
    baseline = plan.baseline_condition_id
    if baseline:
        for condition in plan.conditions:
            if condition.condition_id == baseline:
                continue
            slots = [
                slot
                for slot, values in by_slot.items()
                if values[baseline]["state"] == "valid"
                and values[condition.condition_id]["state"] == "valid"
            ]
            base_pass = sum(
                by_slot[slot][baseline]["task_passed"] is True for slot in slots
            )
            controlled_pass = sum(
                by_slot[slot][condition.condition_id]["task_passed"] is True
                for slot in slots
            )
            pairwise[condition.condition_id] = {
                "baseline_condition_id": baseline,
                "common_valid_slots": len(slots),
                "baseline_success_rate_pct": _rate(base_pass, len(slots)),
                "condition_success_rate_pct": _rate(controlled_pass, len(slots)),
                "delta_percentage_points": (
                    round(100.0 * (controlled_pass - base_pass) / len(slots), 4)
                    if slots
                    else None
                ),
            }

    case_matrix = []
    for case_id in plan.case_ids:
        for seed in plan.seeds:
            values = by_slot[(case_id, seed)]
            case_matrix.append(
                {
                    "case_id": case_id,
                    "selection_stage": plan.selection_stage.get(
                        case_id, "unstratified"
                    ),
                    "seed": seed,
                    "conditions": {
                        condition_id: {
                            "state": values[condition_id]["state"],
                            "task_passed": values[condition_id]["task_passed"],
                            "attempt": values[condition_id]["attempt"],
                        }
                        for condition_id in condition_ids
                    },
                }
            )
    return {
        "panel_root": str(panel_root),
        "cohort_dir": str(plan.cohort),
        "case_count": len(plan.case_ids),
        "seed_count": len(plan.seeds),
        "condition_count": len(plan.conditions),
        "planned_run_count": len(records),
        "baseline_condition_id": baseline,
        "conditions": condition_summaries,
        "all_condition_common_valid": common_summary,
        "pairwise_vs_baseline": pairwise,
        "case_matrix": case_matrix,
    }


def _format_rate(value: object) -> str:
    return "—" if value is None else f"{float(value):.2f}%"


def to_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Type II panel summary",
        "",
        "Infrastructure-invalid and pending slots are excluded from observed rates; "
        "the formal strict rate is shown only for complete conditions.",
        "",
        "| Condition | Valid | Passed | Strict SSR | Observed valid SSR | "
        "PFR | Δ vs baseline | Mean Worker turns |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    pairwise = summary.get("pairwise_vs_baseline") or {}
    for condition_id, row in summary["conditions"].items():
        delta = (pairwise.get(condition_id) or {}).get("delta_percentage_points")
        lines.append(
            (
                "| {condition} | {valid}/{planned} | {passed} | {strict} | "
                "{observed} | {pfr} | {delta} | {turns} |"
            ).format(
                condition=condition_id,
                valid=row["valid"],
                planned=row["planned"],
                passed=row["passed"],
                strict=_format_rate(row["strict_success_rate_pct"]),
                observed=_format_rate(row["observed_valid_success_rate_pct"]),
                pfr=_format_rate(row["protocol_failure_rate_pct"]),
                delta=("—" if delta is None else f"{float(delta):+.2f} pp"),
                turns=(
                    "—"
                    if row["mean_worker_turns"] is None
                    else f"{row['mean_worker_turns']:.2f}"
                ),
            )
        )
    common = summary["all_condition_common_valid"]
    lines.extend(
        [
            "",
            f"All-condition common valid intersection: {common['slot_count']} case-seed slots.",
            "",
            "| Condition | Passed on intersection | Intersection SSR |",
            "| --- | ---: | ---: |",
        ]
    )
    for condition_id, row in common["conditions"].items():
        lines.append(
            f"| {condition_id} | {row['passed']}/{common['slot_count']} | "
            f"{_format_rate(row['success_rate_pct'])} |"
        )
    return "\n".join(lines) + "\n"


def write_csv(path: Path, summary: dict[str, Any]) -> None:
    condition_ids = list(summary["conditions"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["case_id", "selection_stage", "seed", *condition_ids])
        for row in summary["case_matrix"]:
            values = []
            for condition_id in condition_ids:
                condition = row["conditions"][condition_id]
                values.append(
                    "pass"
                    if condition["task_passed"] is True
                    else "fail"
                    if condition["task_passed"] is False
                    else condition["state"]
                )
            writer.writerow(
                [row["case_id"], row["selection_stage"], row["seed"], *values]
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("panel_root", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    parser.add_argument("--csv-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = summarize(args.panel_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"cannot summarize Type II panel: {exc}")
    json_text = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json_text, encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(to_markdown(summary), encoding="utf-8")
    if args.csv_out:
        write_csv(args.csv_out, summary)
    if not any((args.json_out, args.markdown_out, args.csv_out)):
        print(json_text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
