from __future__ import annotations

import json
from pathlib import Path

from looparena.commands.type3_panel import (
    Job,
    _safe_error_text,
    build_command,
    classify,
    load_plan,
)
from looparena.commands.type3_summarize import summarize

ROOT = Path(__file__).resolve().parents[1]


def _plan(tmp_path: Path) -> tuple[Path, Path]:
    cohort = tmp_path / "cohort"
    (cohort / "cases/caseA").mkdir(parents=True)
    (cohort / "cases/caseB").mkdir(parents=True)
    (cohort / "CASE_INDEX.json").write_text(
        json.dumps(
            {
                "cases": [
                    {"case_id": "caseA", "native_task_steps": 2},
                    {"case_id": "caseB", "native_task_steps": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "cohort_dir": str(cohort),
                "assets_root": str(tmp_path / "assets"),
                "cases": "all",
                "seeds": [0, 1],
                "arm": "controlled",
                "worker_model": "worker",
                "controller": {"model": "controller"},
                "execution": {"concurrency": 2},
            }
        ),
        encoding="utf-8",
    )
    return path, cohort


def test_shipped_panel_example_matches_the_public_schema() -> None:
    plan = load_plan(ROOT / "benchmarks/type3/panel.example.json")
    assert plan.case_ids
    assert plan.execution["scbench_runtime_profile"] == "canonical-amd64"


def test_plan_builds_one_official_case_command_per_slot(tmp_path: Path) -> None:
    path, cohort = _plan(tmp_path)
    plan = load_plan(path)
    command = build_command(plan, tmp_path / "panel", Job("caseA", 1), preflight=False)
    assert plan.case_ids == ("caseA", "caseB")
    assert plan.seeds == (0, 1)
    assert str(cohort / "cases/caseA") in command
    assert command[command.index("--worker-model") + 1] == "worker"
    assert command[command.index("--controller-model") + 1] == "controller"
    assert "--resume-existing" not in command


def test_plan_rejects_controller_fields_that_cannot_be_executed(
    tmp_path: Path,
) -> None:
    path, _ = _plan(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["controller"]["transport"] = "gateway"
    path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        load_plan(path)
    except ValueError as exc:
        assert "controller has unknown fields: transport" in str(exc)
    else:
        raise AssertionError("unsupported Controller fields must fail closed")


def test_panel_error_text_redacts_api_credentials(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value-123456")
    assert "secret-value-123456" not in _safe_error_text(
        "request failed for secret-value-123456"
    )


def test_provider_interruption_is_resumable(tmp_path: Path) -> None:
    run = tmp_path / "run"
    child = run / "checkpoints/checkpoint_1"
    child.mkdir(parents=True)
    (run / "workspace").mkdir()
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "infrastructure_invalid",
                "steps": [
                    {"step_id": "checkpoint_1", "status": "infrastructure_invalid"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (child / "run_manifest.json").write_text(
        json.dumps({"termination_reason": "main_worker_provider_failure"}),
        encoding="utf-8",
    )
    (child / "recovery_checkpoint.json").write_text(
        json.dumps({"safe_to_resume": True}), encoding="utf-8"
    )
    assert classify(run) == "resumable"


def test_terminal_manifest_requires_a_consistent_task_outcome(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "run_manifest.json").write_text(
        json.dumps({"status": "completed", "task_passed": None}),
        encoding="utf-8",
    )
    assert classify(run) == "infrastructure_invalid"


def test_summary_does_not_score_missing_runs_as_failures(tmp_path: Path) -> None:
    plan_path, _ = _plan(tmp_path)
    plan = load_plan(plan_path)
    panel = tmp_path / "panel"
    panel.mkdir()
    (panel / "resolved_plan.json").write_text(
        json.dumps(plan.public()), encoding="utf-8"
    )
    run = panel / "results/caseA/seed0"
    run.mkdir(parents=True)
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "task_passed": True,
                "executed_steps": 2,
                "passed_steps": 2,
                "failed_steps": 0,
                "total_tokens": 100,
            }
        ),
        encoding="utf-8",
    )
    result = summarize(panel)
    assert result["planned"] == 4
    assert result["valid"] == 1
    assert result["passed"] == 1
    assert result["strict_success_rate"] is None
    assert result["observed_valid_success_rate"] == 1.0
    assert result["reported_total_tokens"] is None
    assert result["token_complete_runs"] == 1

    for case_id, seed in (("caseA", 1), ("caseB", 0), ("caseB", 1)):
        other = panel / f"results/{case_id}/seed{seed}"
        other.mkdir(parents=True)
        (other / "run_manifest.json").write_text(
            json.dumps(
                {
                    "status": "failed",
                    "task_passed": False,
                    "executed_steps": 1,
                    "passed_steps": 0,
                    "failed_steps": 1,
                    "total_tokens": 100,
                }
            ),
            encoding="utf-8",
        )
    complete = summarize(panel)
    assert complete["complete"] is True
    assert complete["reported_total_tokens"] == 400
