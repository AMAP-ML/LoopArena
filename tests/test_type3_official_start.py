from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from looparena.benchmarks.type3 import (
    all_ordered_checkpoints,
    canonical_records,
    render_scbench_spec,
    validate_type3_cohort,
)
from looparena.commands.type3_run import (
    aggregate_model_token_total,
    audited_completed_steps,
    audited_provider_interrupted_step,
    continue_after_step,
    final_step_state,
    load_case,
    model_token_total_from_manifest,
    prepare_provider_interrupted_step_resume,
    verified_beyondswe_submodules,
)
from looparena.commands.type3_run import (
    build_parser as build_type3_runner_parser,
)
from looparena.commands.type3_run import (
    main as run_type3_main,
)
from looparena.runtime.sandbox import (
    RestoreFidelityError,
    sanitize_workspace_git_history,
)

TYPE3 = ROOT / "benchmarks" / "type3"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def initialize_git_repository(repository: Path) -> None:
    repository.mkdir()
    run_git(repository, "init", "--quiet")
    run_git(repository, "config", "user.email", "type3-test@example.com")
    run_git(repository, "config", "user.name", "Type III Test")


def test_render_scbench_spec_uses_official_visible_form() -> None:
    raw = (
        "<!-- private canary -->\n"
        "Run %%%ENTRYPOINT:entry_file%%% with "
        "%%%ENTRYPOINT:entry_command%%% now.\n"
        "<!-- public specification comment -->\n"
    )
    assert render_scbench_spec(raw, "tool") == (
        "Run tool.py with python tool.py now.\n<!-- public specification comment -->\n"
    )


def test_canonical_records_keep_first_case_and_record_legacy_alias() -> None:
    records = [
        {"case_id": "case001", "official_task_id": "scbench:meshctl"},
        {"case_id": "case002", "official_task_id": "scbench:other"},
        {"case_id": "case201", "official_task_id": "scbench:meshctl"},
    ]
    selected, aliases = canonical_records(records)
    assert [row["case_id"] for row in selected] == ["case001", "case002"]
    assert aliases == {"case201": "case001"}


def test_type3_runner_accepts_fixed_control_only_for_controlled_runs() -> None:
    parser = build_type3_runner_parser()
    args = parser.parse_args(
        [
            "--case-dir",
            str(TYPE3 / "cases" / "case004"),
            "--arm",
            "controlled",
            "--assets-root",
            "/tmp/type3-assets",
            "--controller-provider",
            "non-adaptive-fixed",
            "--preflight-only",
        ]
    )
    assert args.controller_provider == "non-adaptive-fixed"
    with pytest.raises(SystemExit, match="controlled-only"):
        run_type3_main(
            [
                "--case-dir",
                str(TYPE3 / "cases" / "case004"),
                "--arm",
                "no-control",
                "--assets-root",
                "/tmp/type3-assets",
                "--controller-provider",
                "non-adaptive-fixed",
                "--preflight-only",
            ]
        )


def test_type3_token_accounting_sums_complete_model_usage() -> None:
    manifest = {
        "compute_accounting": {
            "main_worker": {
                "tokens": {
                    "request_count": 3,
                    "usage_reported_request_count": 3,
                    "total_tokens": 100,
                }
            },
            "controlled_only": {
                "reporter_tokens": {
                    "request_count": 1,
                    "usage_reported_request_count": 1,
                    "total_tokens": 20,
                },
                "controller_tokens": {
                    "request_count": 0,
                    "usage_reported_request_count": 0,
                    "total_tokens": None,
                },
            },
        }
    }
    assert model_token_total_from_manifest(manifest) == 120
    assert (
        aggregate_model_token_total([{"total_tokens": 120}, {"total_tokens": 30}])
        == 150
    )


def test_type3_token_accounting_fails_closed_on_partial_usage() -> None:
    manifest = {
        "compute_accounting": {
            "main_worker": {
                "tokens": {
                    "request_count": 3,
                    "usage_reported_request_count": 3,
                    "total_tokens": 100,
                }
            },
            "controlled_only": {
                "reporter_tokens": {
                    "request_count": 2,
                    "usage_reported_request_count": 1,
                    "total_tokens": 20,
                },
                "controller_tokens": {
                    "request_count": 0,
                    "usage_reported_request_count": 0,
                    "total_tokens": None,
                },
            },
        }
    }
    assert model_token_total_from_manifest(manifest) is None
    assert (
        aggregate_model_token_total([{"total_tokens": 100}, {"total_tokens": None}])
        is None
    )


def test_all_ordered_checkpoints_requires_the_complete_contiguous_series() -> None:
    config = {
        "checkpoints": {
            "checkpoint_1": {"order": 1},
            "checkpoint_2": {"order": 2},
            "checkpoint_3": {"order": 3},
        }
    }
    assert all_ordered_checkpoints(config) == [
        "checkpoint_1",
        "checkpoint_2",
        "checkpoint_3",
    ]
    config["checkpoints"].pop("checkpoint_2")
    with pytest.raises(ValueError, match="noncontiguous"):
        all_ordered_checkpoints(config)


def test_git_history_sanitizer_preserves_and_cleans_initialized_submodule(
    tmp_path: Path,
) -> None:
    child_source = tmp_path / "llhttp-source"
    initialize_git_repository(child_source)
    (child_source / "README.md").write_text("pinned source\n", encoding="utf-8")
    run_git(child_source, "add", "README.md")
    run_git(child_source, "commit", "--quiet", "-m", "pinned child")
    run_git(child_source, "tag", "leaky-child-tag")
    child_head = run_git(child_source, "rev-parse", "HEAD")

    workspace = tmp_path / "aiohttp"
    initialize_git_repository(workspace)
    (workspace / "README.md").write_text("superproject\n", encoding="utf-8")
    run_git(workspace, "add", "README.md")
    run_git(workspace, "commit", "--quiet", "-m", "initial parent")
    run_git(
        workspace,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(child_source),
        "vendor/llhttp",
    )
    run_git(workspace, "commit", "--quiet", "-am", "pin child")
    run_git(workspace, "remote", "add", "origin", str(tmp_path / "unused"))
    run_git(workspace, "tag", "leaky-parent-tag")
    parent_head = run_git(workspace, "rev-parse", "HEAD")
    submodule = workspace / "vendor" / "llhttp"
    gitfile = submodule / ".git"

    assert gitfile.is_file()
    assert verified_beyondswe_submodules(workspace) == [
        {"path": "vendor/llhttp", "commit": child_head}
    ]
    assert run_git(workspace, "remote") == "origin"
    assert run_git(submodule, "remote") == "origin"

    run_git(submodule, "config", "user.email", "type3-test@example.com")
    run_git(submodule, "config", "user.name", "Type III Test")
    (submodule / "README.md").write_text("unpinned source\n", encoding="utf-8")
    run_git(submodule, "commit", "--quiet", "-am", "unpinned child")
    with pytest.raises(RuntimeError, match="not initialized at its gitlink"):
        verified_beyondswe_submodules(workspace)
    run_git(submodule, "checkout", "--quiet", "--detach", child_head)

    sanitize_workspace_git_history(workspace)

    assert gitfile.is_file()
    assert run_git(workspace, "rev-parse", "HEAD") == parent_head
    assert run_git(submodule, "rev-parse", "HEAD") == child_head
    assert verified_beyondswe_submodules(workspace) == [
        {"path": "vendor/llhttp", "commit": child_head}
    ]
    assert run_git(workspace, "status", "--porcelain", "--ignore-submodules=none") == ""
    assert run_git(workspace, "remote") == ""
    assert run_git(submodule, "remote") == ""
    assert run_git(workspace, "for-each-ref", "--format=%(refname)") == ""
    assert run_git(submodule, "for-each-ref", "--format=%(refname)") == ""
    assert Path(run_git(submodule, "rev-parse", "--absolute-git-dir")) == (
        workspace / ".git" / "modules" / "vendor" / "llhttp"
    )


def test_git_history_sanitizer_rejects_gitfile_outside_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    initialize_git_repository(outside)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").write_text("gitdir: ../outside/.git\n", encoding="utf-8")

    with pytest.raises(
        RestoreFidelityError,
        match="Git directory escapes the workspace",
    ):
        sanitize_workspace_git_history(workspace)


def test_type3_cohort_has_27_canonical_complete_official_tasks() -> None:
    observed = validate_type3_cohort(TYPE3)
    assert observed == {
        "total": 27,
        "scbench": 11,
        "beyondswe_harbor": 16,
        "native_task_steps": 81,
        "compatibility_aliases": 4,
    }
    index = read_json(TYPE3 / "CASE_INDEX.json")
    assert index["compatibility_aliases"] == {
        "case011": "case003",
        "case015": "case005",
        "case115": "case004",
        "case201": "case001",
    }
    assert len(index["cases"]) == 27
    assert len({row["official_task_id"] for row in index["cases"]}) == 27
    case_dirs = sorted(path for path in (TYPE3 / "cases").iterdir() if path.is_dir())
    assert len(case_dirs) == 27
    for case_dir in case_dirs:
        assert not (case_dir / "workspace.tar.gz").exists()
        assert not (case_dir / "public_messages.json").exists()
        manifest, sequence = load_case(case_dir)
        assert manifest["official_task_id"] == sequence["official_task_id"]
        assert "sample_id" not in manifest
        assert "source_type2_case_ref" not in manifest


def test_all_formal_evaluator_plans_exclude_looparena_revisions() -> None:
    registry = read_json(TYPE3 / "BEYONDSWE_OFFICIAL_EVALUATORS.json")
    source_revision = registry["source"]["revision"]
    observed_beyondswe: set[str] = set()
    for plan_path in sorted((TYPE3 / "cases").glob("*/evaluator_plans/*.json")):
        plan = read_json(plan_path)
        assert "evaluator_revision" not in plan
        if plan["adapter_kind"] != "beyondswe_harbor":
            continue
        case_id = plan_path.parents[1].name
        binding = registry["cases"][case_id]
        assert plan["source_revision"] == source_revision
        assert plan["adapter_config"]["task_dir"] == binding["asset_task_dir"]
        assert len(binding["source_tree_oid"]) == 40
        observed_beyondswe.add(case_id)
    assert observed_beyondswe == set(registry["cases"])


def test_scbench_sequences_cover_every_counted_official_checkpoint() -> None:
    for case_dir in sorted((TYPE3 / "cases").iterdir()):
        manifest = read_json(case_dir / "case.json")
        if manifest["adapter_kind"] != "scbench":
            continue
        sequence = read_json(case_dir / "task_sequence.json")
        names = [step["step_id"] for step in sequence["steps"]]
        assert names == [f"checkpoint_{index}" for index in range(1, len(names) + 1)]
        assert sequence["target_step"] == names[-1]
        assert sequence["official_checkpoint_count"] == len(names)
        assert sequence["checkpoint_coverage_policy"] == "all_official_checkpoints"
        assert sequence["checkpoint_context_policy"] == (
            "reset_model_context_preserve_workspace"
        )
        assert sequence["checkpoint_failure_policy"] == (
            "continue_after_valid_task_failure_stop_on_infrastructure_invalid"
        )
        assert sequence["aggregate_pass_policy"] == "all_official_checkpoints_pass"
        for step in sequence["steps"]:
            task_text = (case_dir / step["task_ref"]).read_text(encoding="utf-8")
            assert not task_text.startswith("<!--")
            assert "%%%ENTRYPOINT:" not in task_text


def test_beyondswe_is_one_episode_from_a_pinned_parent_commit() -> None:
    for case_dir in sorted((TYPE3 / "cases").iterdir()):
        manifest = read_json(case_dir / "case.json")
        if manifest["adapter_kind"] != "beyondswe_harbor":
            continue
        sequence = read_json(case_dir / "task_sequence.json")
        assert sequence["workspace_origin"] == "pinned_source_image_parent_commit"
        assert len(sequence["official_parent_commit"]) == 40
        assert len(sequence["steps"]) == 1
        assert sequence["task_scope"] == "official_full_task"
        assert sequence["official_task_id"].startswith("beyondswe:")


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (
            {
                "infrastructure_validity": {"valid": True},
                "evaluation_state": "completed",
                "task_outcome": {"success_at_budget": True},
            },
            ("passed", True),
        ),
        (
            {
                "infrastructure_validity": {"valid": True},
                "evaluation_state": "completed",
                "task_outcome": {"success_at_budget": False},
            },
            ("failed", False),
        ),
        (
            {
                "infrastructure_validity": {"valid": False},
                "evaluation_state": "invalid",
                "task_outcome": {"success_at_budget": None},
            },
            ("infrastructure_invalid", None),
        ),
    ],
)
def test_checkpoint_gate_distinguishes_task_failure_from_infrastructure(
    manifest: dict, expected: tuple[str, bool | None]
) -> None:
    assert final_step_state(manifest) == expected


def test_full_scbench_series_continues_after_valid_failure_only() -> None:
    sequence = {
        "checkpoint_failure_policy": (
            "continue_after_valid_task_failure_stop_on_infrastructure_invalid"
        )
    }
    assert continue_after_step(sequence, "passed") is True
    assert continue_after_step(sequence, "failed") is True
    assert continue_after_step(sequence, "infrastructure_invalid") is False
    assert (
        continue_after_step({"checkpoint_failure_policy": "stop_after_task"}, "failed")
        is False
    )


def test_resume_reuses_only_audited_completed_official_steps(
    tmp_path: Path,
) -> None:
    case_dir = TYPE3 / "cases" / "case203"
    manifest, sequence = load_case(case_dir)
    out_dir = tmp_path / "run"
    (out_dir / "workspace").mkdir(parents=True)
    child_dir = out_dir / "checkpoints" / "checkpoint_1"
    child_dir.mkdir(parents=True)
    child = {
        "infrastructure_validity": {"valid": True},
        "evaluation_state": "completed",
        "task_outcome": {"success_at_budget": False},
        "task_sha256": sequence["steps"][0]["task_sha256"],
        "compute_accounting": {
            "main_worker": {
                "tokens": {
                    "request_count": 2,
                    "usage_reported_request_count": 2,
                    "total_tokens": 100,
                }
            },
            "controlled_only": {
                "reporter_tokens": {
                    "request_count": 1,
                    "usage_reported_request_count": 1,
                    "total_tokens": 20,
                },
                "controller_tokens": {
                    "request_count": 0,
                    "usage_reported_request_count": 0,
                    "total_tokens": None,
                },
            },
        },
    }
    (child_dir / "run_manifest.json").write_text(json.dumps(child), encoding="utf-8")
    aggregate = {
        "case_id": "case203",
        "arm": "controlled",
        "seed": 0,
        "episode_origin": "official_problem_start",
        "steps": [
            {
                "step_id": "checkpoint_1",
                "status": "failed",
                "task_passed": False,
                "total_tokens": None,
            }
        ],
    }
    audited = audited_completed_steps(
        out_dir=out_dir,
        aggregate=aggregate,
        manifest=manifest,
        sequence=sequence,
        arm="controlled",
        seed=0,
    )
    assert audited == [{**aggregate["steps"][0], "total_tokens": 120}]
    assert aggregate["steps"][0]["total_tokens"] is None

    child["task_sha256"] = "0" * 64
    (child_dir / "run_manifest.json").write_text(json.dumps(child), encoding="utf-8")
    with pytest.raises(ValueError, match="resume_task_hash_mismatch"):
        audited_completed_steps(
            out_dir=out_dir,
            aggregate=aggregate,
            manifest=manifest,
            sequence=sequence,
            arm="controlled",
            seed=0,
        )


def test_resume_archives_and_reuses_provider_interrupted_checkpoint(
    tmp_path: Path,
) -> None:
    case_dir = TYPE3 / "cases" / "case004"
    manifest, sequence = load_case(case_dir)
    out_dir = tmp_path / "run"
    (out_dir / "workspace").mkdir(parents=True)
    step_id = "checkpoint_1"
    child_dir = out_dir / "checkpoints" / step_id
    child_dir.mkdir(parents=True)
    (child_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "termination_reason": "main_worker_provider_failure",
                "infrastructure_validity": {"valid": False},
            }
        ),
        encoding="utf-8",
    )
    checkpoint = {
        "arm": "controlled",
        "sample_id": "scbench:file_backup:checkpoint_1",
        "seed": 0,
        "start_mode": "bootstrap_contract_start",
        "task_sha256": sequence["steps"][0]["task_sha256"],
        "safe_to_resume": True,
        "phase": "call_worker",
        "cycle_index": 1,
        "main_worker_turns": 7,
        "runtime_identity": {
            "worker": {"model": "qwen3.7-plus"},
            "controller": {
                "provider_kind": "non-adaptive-fixed",
                "model": "looparena.non_adaptive_fixed_goal",
            },
        },
    }
    (child_dir / "recovery_checkpoint.json").write_text(
        json.dumps(checkpoint), encoding="utf-8"
    )
    aggregate = {
        "case_id": "case004",
        "arm": "controlled",
        "seed": 0,
        "episode_origin": "official_problem_start",
        "steps": [
            {
                "step_id": step_id,
                "status": "infrastructure_invalid",
                "task_passed": None,
            }
        ],
    }

    assert audited_provider_interrupted_step(
        out_dir=out_dir,
        aggregate=aggregate,
        manifest=manifest,
        sequence=sequence,
        arm="controlled",
        seed=0,
        worker_model="qwen3.7-plus",
        controller_provider="non-adaptive-fixed",
        controller_model="looparena.non_adaptive_fixed_goal",
    ) == ([], step_id)
    archived = prepare_provider_interrupted_step_resume(
        out_dir=out_dir, step_id=step_id
    )
    assert archived.name == "checkpoint_1.provider-failure-attempt-1"
    assert (archived / "run_manifest.json").is_file()
    assert not (child_dir / "run_manifest.json").exists()
    assert read_json(child_dir / "resumed_from.json")["source_worker_turns"] == 7
