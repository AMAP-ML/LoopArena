"""Summarize one Type III panel without scoring infrastructure gaps as failures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from looparena.commands.type3_panel import Job, Plan, _read, classify, run_dir


def summarize(panel_root: Path) -> dict[str, Any]:
    panel_root = panel_root.expanduser().resolve()
    resolved = _read(panel_root / "resolved_plan.json")
    plan = Plan(
        source=Path(str(resolved["source_plan"])),
        cohort=Path(str(resolved["cohort_dir"])),
        assets_root=Path(str(resolved["assets_root"])),
        case_ids=tuple(resolved["case_ids"]),
        seeds=tuple(resolved["seeds"]),
        arm=str(resolved["arm"]),
        worker_model=str(resolved["worker_model"]),
        controller=dict(resolved.get("controller") or {}),
        execution=dict(resolved["execution"]),
    )
    records: list[dict[str, Any]] = []
    for case_id in plan.case_ids:
        for seed in plan.seeds:
            job = Job(case_id, seed)
            path = run_dir(panel_root, job)
            state = classify(path)
            record: dict[str, Any] = {"case_id": case_id, "seed": seed, "state": state}
            if state == "valid":
                manifest = _read(path / "run_manifest.json")
                record.update(
                    {
                        "task_passed": manifest.get("task_passed"),
                        "executed_steps": manifest.get("executed_steps"),
                        "passed_steps": manifest.get("passed_steps"),
                        "failed_steps": manifest.get("failed_steps"),
                        "total_tokens": manifest.get("total_tokens"),
                    }
                )
            records.append(record)
    valid = [record for record in records if record["state"] == "valid"]
    passed = sum(record.get("task_passed") is True for record in valid)
    complete = len(valid) == len(records)
    token_values = [record.get("total_tokens") for record in valid]
    token_complete = complete and all(
        isinstance(value, int) and not isinstance(value, bool) for value in token_values
    )
    return {
        "planned": len(records),
        "valid": len(valid),
        "passed": passed,
        "failed": len(valid) - passed,
        "resumable": sum(record["state"] == "resumable" for record in records),
        "infrastructure_invalid": sum(
            record["state"] == "infrastructure_invalid" for record in records
        ),
        "pending": sum(record["state"] == "pending" for record in records),
        "complete": complete,
        "strict_success_rate": passed / len(records) if complete else None,
        "observed_valid_success_rate": passed / len(valid) if valid else None,
        "reported_total_tokens": (
            sum(int(value) for value in token_values) if token_complete else None
        ),
        "token_complete_runs": sum(
            isinstance(record.get("total_tokens"), int)
            and not isinstance(record.get("total_tokens"), bool)
            for record in valid
        ),
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = summarize(args.panel_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"cannot summarize Type III panel: {exc}")
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
