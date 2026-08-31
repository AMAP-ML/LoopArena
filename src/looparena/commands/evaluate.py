#!/usr/bin/env python3
"""Evaluate a sealed solve result with a benchmark-specific official adapter."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from looparena.evaluators import evaluate_with_plan
from looparena.harness.evaluator_protocol import (
    combine_final_infrastructure_validity,
    derive_final_task_outcome,
    evaluator_identity,
    validate_evaluator_plan,
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _write_json_atomic(path: Path, value: Any) -> None:
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def evaluate_run(
    *,
    run_dir: Path,
    plan: dict[str, Any],
) -> dict[str, Any]:
    run_dir = Path(run_dir).resolve()
    plan_errors = validate_evaluator_plan(plan)
    if plan_errors:
        raise ValueError("invalid evaluator plan: " + ",".join(plan_errors))
    if plan.get("adapter_kind") == "beyondswe_harbor":
        raise ValueError(
            "BeyondSWE formal evaluation must run in the live solve container "
            "after model access ends; a sealed workspace alone does not retain "
            "container runtime state"
        )
    solve_path = run_dir / "solve_manifest.json"
    if not solve_path.is_file():
        raise ValueError("solve_manifest.json is required for official evaluation")
    solve_manifest = _read_json(solve_path)
    current_manifest = _read_json(run_dir / "run_manifest.json")
    if current_manifest != solve_manifest:
        raise ValueError("run manifest changed after solve sealing")
    workspace = run_dir / "solve_final_workspace"
    if not workspace.is_dir():
        raise ValueError(
            "sealed solve_final_workspace is required for official evaluation"
        )
    evaluation_root = run_dir / "evaluation"
    plan_path = run_dir / "evaluator_plan.json"
    receipt_path = run_dir / "terminal_external_evaluator.json"
    if evaluation_root.exists() or plan_path.exists() or receipt_path.exists():
        raise ValueError("evaluation output already exists; receipts are immutable")
    with tempfile.TemporaryDirectory(prefix="looparena-evaluator-") as temporary:
        evaluation_workspace = Path(temporary) / "workspace"
        shutil.copytree(workspace, evaluation_workspace, symlinks=True)
        receipt = evaluate_with_plan(
            workspace=evaluation_workspace,
            output_dir=evaluation_root,
            plan=plan,
        )
    return attach_evaluation_receipt(
        run_dir=run_dir,
        plan=plan,
        receipt=receipt,
    )


def attach_evaluation_receipt(
    *,
    run_dir: Path,
    plan: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Attach one already-completed official evaluation to a sealed solve."""

    run_dir = Path(run_dir).resolve()
    plan_errors = validate_evaluator_plan(plan)
    if plan_errors:
        raise ValueError("invalid evaluator plan: " + ",".join(plan_errors))
    solve_path = run_dir / "solve_manifest.json"
    if not solve_path.is_file():
        raise ValueError("solve_manifest.json is required for official evaluation")
    solve_manifest = _read_json(solve_path)
    current_manifest = _read_json(run_dir / "run_manifest.json")
    if current_manifest != solve_manifest:
        raise ValueError("run manifest changed after solve sealing")
    if not (run_dir / "solve_final_workspace").is_dir():
        raise ValueError(
            "sealed solve_final_workspace is required for official evaluation"
        )
    plan_path = run_dir / "evaluator_plan.json"
    receipt_path = run_dir / "terminal_external_evaluator.json"
    if plan_path.exists() or receipt_path.exists():
        raise ValueError("evaluation receipt already exists; receipts are immutable")
    if not (run_dir / "evaluation").is_dir():
        raise ValueError("official evaluation artifact directory is missing")

    _write_json_atomic(plan_path, plan)
    _write_json_atomic(receipt_path, receipt)
    final_manifest = json.loads(json.dumps(solve_manifest))
    final_manifest["solve_infrastructure_validity"] = solve_manifest.get(
        "infrastructure_validity"
    )
    final_manifest["terminal_external_evaluator"] = receipt
    final_manifest["terminal_evaluator_identity"] = evaluator_identity(plan)
    final_manifest["official_evaluator_ran_during_solve"] = (
        receipt.get("solve_sandbox_stopped_before_evaluator") is False
    )
    final_manifest["infrastructure_validity"] = combine_final_infrastructure_validity(
        final_manifest["solve_infrastructure_validity"], receipt
    )
    final_manifest["task_outcome"] = derive_final_task_outcome(final_manifest, receipt)
    final_manifest["evaluation_state"] = (
        "completed"
        if final_manifest["infrastructure_validity"]["evaluator_valid"]
        else "invalid"
    )
    final_manifest["artifacts"]["solve_manifest"] = "solve_manifest.json"
    final_manifest["artifacts"]["evaluator_plan"] = "evaluator_plan.json"
    final_manifest["artifacts"]["terminal_external_evaluator"] = (
        "terminal_external_evaluator.json"
    )
    final_manifest["artifacts"]["evaluation"] = "evaluation"
    _write_json_atomic(run_dir / "run_manifest.json", final_manifest)
    return final_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--evaluator-plan", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = evaluate_run(
        run_dir=args.run_dir,
        plan=_read_json(args.evaluator_plan),
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
