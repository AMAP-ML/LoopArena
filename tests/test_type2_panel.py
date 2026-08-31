from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from looparena.commands import type2_panel as panel
from looparena.commands import type2_summarize as panel_summary
from looparena.harness.evaluator_protocol import classify_infrastructure_validity


class Type2PanelTest(unittest.TestCase):
    def test_shipped_panel_example_matches_the_public_schema(self) -> None:
        plan = panel.load_plan(ROOT / "benchmarks/type2/panel.example.json")
        self.assertEqual(plan.execution.scbench_runtime_profile, "canonical-amd64")
        self.assertTrue(plan.case_ids)

    def _fixture(self, root: Path) -> tuple[Path, panel.PanelPlan]:
        cohort = root / "cohort"
        (cohort / "cases" / "caseA").mkdir(parents=True)
        (cohort / "cases" / "caseB").mkdir(parents=True)
        (cohort / "CASE_INDEX.json").write_text(
            json.dumps(
                {
                    "cases": [
                        {"case_id": "caseA", "selection_stage": "middle"},
                        {"case_id": "caseB", "selection_stage": "late"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        plan_path = root / "panel.json"
        plan_path.write_text(
            json.dumps(
                {
                    "cohort_dir": str(cohort),
                    "cases": "all",
                    "seeds": [0, 1],
                    "worker": {"model": "qwen-worker"},
                    "gateway_profiles": [
                        {
                            "id": "gateway-primary",
                            "api_key_env": "TEST_PANEL_PRIMARY_KEY",
                            "base_url_env": "TEST_PANEL_BASE_URL",
                        },
                        {
                            "id": "gateway-secondary",
                            "api_key_env": "TEST_PANEL_SECONDARY_KEY",
                            "base_url_env": "TEST_PANEL_BASE_URL",
                        },
                    ],
                    "conditions": [
                        {"id": "no-control", "arm": "no-control"},
                        {
                            "id": "qwen-control",
                            "arm": "controlled",
                            "controller": {
                                "model": "qwen-controller",
                            },
                        },
                        {
                            "id": "fixed-control",
                            "arm": "controlled",
                            "controller": {"provider": "non-adaptive-fixed"},
                        },
                        {
                            "id": "gpt-control",
                            "arm": "controlled",
                            "controller": {
                                "model": "gpt-controller",
                            },
                        },
                        {
                            "id": "claude-control",
                            "arm": "controlled",
                            "controller": {
                                "model": "claude-controller",
                            },
                        },
                    ],
                    "execution": {
                        "concurrency": 3,
                        "retry_delay_sec": 0,
                        "continue_on_preflight_failure": True,
                    },
                    "baseline_condition_id": "no-control",
                }
            ),
            encoding="utf-8",
        )
        return plan_path, panel.load_plan(plan_path)

    def test_dynamic_cases_conditions_and_seeds_define_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, plan = self._fixture(Path(temporary))
            self.assertEqual(plan.case_ids, ("caseA", "caseB"))
            self.assertEqual(len(panel.jobs_for_plan(plan)), 2 * 5 * 2)
            fixed = plan.condition("fixed-control")
            self.assertEqual(fixed.provider, "non-adaptive-fixed")
            self.assertEqual(fixed.model, panel.FIXED_POLICY_ID)
            self.assertEqual(fixed.transport, "local-deterministic")
            self.assertIsNone(fixed.credential_profile_id)
            public = {row["id"]: row for row in plan.public_dict()["conditions"]}
            self.assertNotIn("provider", public["qwen-control"]["controller"])
            self.assertNotIn("transport", public["qwen-control"]["controller"])
            self.assertEqual(
                public["fixed-control"]["controller"]["provider"],
                "non-adaptive-fixed",
            )
            self.assertIsNone(plan.condition("gpt-control").credential_profile_id)
            self.assertIsNone(plan.condition("claude-control").credential_profile_id)

    def test_preflight_is_fail_closed_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan_path, _ = self._fixture(Path(temporary))
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["execution"].pop("continue_on_preflight_failure")
            plan_path.write_text(json.dumps(raw), encoding="utf-8")
            plan = panel.load_plan(plan_path)
            self.assertFalse(plan.execution.continue_on_preflight_failure)

    def test_preflight_only_reports_unavailable_docker_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, _ = self._fixture(root)
            assets_root = root / "assets"
            assets_root.mkdir()
            output_root = root / "output"
            environment = {
                "TEST_PANEL_PRIMARY_KEY": "test-primary",
                "TEST_PANEL_SECONDARY_KEY": "test-secondary",
                "TEST_PANEL_BASE_URL": "https://example.invalid/v1",
            }
            with (
                mock.patch.dict(os.environ, environment, clear=False),
                mock.patch.object(panel, "docker_available", return_value=False),
            ):
                status = panel.main(
                    [
                        "--plan",
                        str(plan_path),
                        "--out-dir",
                        str(output_root),
                        "--assets-root",
                        str(assets_root),
                        "--preflight-only",
                        "--allow-dirty",
                    ]
                )

            self.assertEqual(status, 2)
            receipt = json.loads(
                (output_root / "preflight.json").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["stage"], "docker_unavailable")
            self.assertFalse(receipt["docker_ready"])

    def test_unsupported_runtime_profile_is_rejected_by_the_panel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan_path, _ = self._fixture(Path(temporary))
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["execution"]["scbench_runtime_profile"] = "mac-arm64"
            plan_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scbench_runtime_profile"):
                panel.load_plan(plan_path)

    def test_openai_profile_uses_the_public_default_when_base_url_is_omitted(
        self,
    ) -> None:
        profile = panel.GatewayProfile(
            profile_id="openai-compatible",
            api_key_env="OPENAI_API_KEY",
            base_url_env="OPENAI_BASE_URL",
        )
        with mock.patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-key"},
            clear=True,
        ):
            self.assertEqual(
                panel._profile_values(profile),
                ("test-key", "https://api.openai.com/v1"),
            )

    def test_commands_use_the_gateway_for_model_controllers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, plan = self._fixture(root)
            profile = plan.profiles[0]
            common = {
                "plan": plan,
                "attempt": root / "attempt-001",
                "assets_root": root / "assets",
                "primary": profile,
                "base_url": "https://gateway.example/v1",
                "resume_source": None,
            }
            no_control = panel.build_case_command(
                job=panel.Job("caseA", "no-control", 0), **common
            )
            gpt = panel.build_case_command(
                job=panel.Job("caseA", "gpt-control", 0), **common
            )
            claude = panel.build_case_command(
                job=panel.Job("caseA", "claude-control", 0), **common
            )
            fixed = panel.build_case_command(
                job=panel.Job("caseA", "fixed-control", 0), **common
            )
            fixed_resume = panel.build_case_command(
                job=panel.Job("caseA", "fixed-control", 0),
                resume_source=root / "attempt-000",
                **{
                    key: value
                    for key, value in common.items()
                    if key != "resume_source"
                },
            )
            self.assertNotIn("--controller-transport", no_control)
            self.assertEqual(
                fixed[fixed.index("--controller-provider") + 1],
                "non-adaptive-fixed",
            )
            self.assertNotIn("--controller-model", fixed)
            self.assertNotIn("--controller-transport", fixed)
            self.assertIn("--resume-from-attempt", fixed_resume)
            self.assertNotIn("--controller-transport", gpt)
            self.assertNotIn("--controller-transport", claude)
            self.assertIn("--controller-base-url", gpt)
            self.assertIn("--controller-base-url", claude)

    def test_fixed_controller_rejects_model_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, _ = self._fixture(root)
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            fixed = next(
                row for row in raw["conditions"] if row["id"] == "fixed-control"
            )
            fixed["controller"]["model"] = "unused"
            plan_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "has no model settings"):
                panel.load_plan(plan_path)

    def test_fixed_controller_manifest_identity_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, plan = self._fixture(Path(temporary))
            job = panel.Job("caseA", "fixed-control", 0)
            manifest = {
                "arm": "controlled",
                "seed": 0,
                "worker": {"model": "qwen-worker"},
                "controller": {
                    "provider_kind": "non-adaptive-fixed",
                    "model": panel.FIXED_POLICY_ID,
                    "transport": "local-deterministic",
                },
            }
            self.assertTrue(panel._manifest_identity_matches(manifest, plan, job))
            manifest["controller"]["provider_kind"] = "model"
            self.assertFalse(panel._manifest_identity_matches(manifest, plan, job))

    def test_gateway_controller_can_use_a_separate_endpoint_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path, _ = self._fixture(root)
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["controller_gateway_profile"] = {
                "id": "controller-internal-gateway",
                "api_key_env": "TEST_CONTROLLER_KEY",
                "base_url_env": "TEST_CONTROLLER_BASE_URL",
            }
            plan_path.write_text(json.dumps(raw), encoding="utf-8")
            plan = panel.load_plan(plan_path)
            controller_profile = plan.controller_gateway_profile
            self.assertIsNotNone(controller_profile)
            command = panel.build_case_command(
                plan=plan,
                job=panel.Job("caseA", "qwen-control", 0),
                attempt=root / "attempt-001",
                assets_root=root / "assets",
                primary=plan.profiles[0],
                base_url="https://worker.example/v1",
                controller_profile=controller_profile,
                controller_base_url="https://controller.example/v1",
            )
            self.assertEqual(
                command[command.index("--controller-base-url") + 1],
                "https://controller.example/v1",
            )
            self.assertEqual(
                command[command.index("--controller-api-key-env") + 1],
                "TEST_CONTROLLER_KEY",
            )
            self.assertEqual(
                command[command.index("--controller-credential-profile-id") + 1],
                "controller-internal-gateway",
            )

            scheduler_command = panel.redact_command_endpoints(command)
            self.assertEqual(
                scheduler_command[scheduler_command.index("--base-url") + 1],
                "[REDACTED]",
            )
            self.assertEqual(
                scheduler_command[scheduler_command.index("--controller-base-url") + 1],
                "[REDACTED]",
            )
            self.assertNotIn("worker.example", " ".join(scheduler_command))
            self.assertNotIn("controller.example", " ".join(scheduler_command))
            self.assertIn("https://worker.example/v1", command)
            self.assertIn("https://controller.example/v1", command)

    def test_provider_checkpoint_resumes_and_valid_failure_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, plan = self._fixture(root)
            job = panel.Job("caseA", "no-control", 0)
            attempt = root / "attempt-001"
            attempt.mkdir()
            (root / "attempt-001.workspace").mkdir()
            manifest = {
                "arm": "no-control",
                "seed": 0,
                "worker": {"model": "qwen-worker"},
                "controller": None,
                "termination_reason": "main_worker_provider_failure",
            }
            (attempt / "solve_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (attempt / "recovery_checkpoint.json").write_text(
                json.dumps({"safe_to_resume": True}), encoding="utf-8"
            )
            allowed, source, reason = panel._retry_decision(
                root, plan.execution, plan, job
            )
            self.assertTrue(allowed)
            self.assertEqual(source, attempt)
            self.assertEqual(reason, "safe_provider_checkpoint")
            command = panel.build_case_command(
                plan=plan,
                job=job,
                attempt=root / "attempt-002",
                assets_root=root / "assets",
                primary=plan.profiles[0],
                base_url="https://gateway.example/v1",
                resume_source=source,
            )
            self.assertEqual(
                command[command.index("--resume-from-attempt") + 1],
                str(attempt),
            )

            (attempt / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with mock.patch.object(
                panel,
                "validate_run_dir",
                return_value={
                    "ok": True,
                    "infrastructure_valid": True,
                    "task_success_at_budget": False,
                },
            ):
                valid = panel.latest_valid(root, plan, job)
            self.assertIs(valid["task_passed"], False)

    def test_wall_time_exhaustion_is_infrastructure_invalid_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, plan = self._fixture(root)
            job = panel.Job("caseA", "no-control", 0)
            attempt = root / "attempt-001"
            attempt.mkdir()
            (root / "attempt-001.workspace").mkdir()
            manifest = {
                "arm": "no-control",
                "seed": 0,
                "status": "runtime_exceeded",
                "worker": {"model": "qwen-worker"},
                "controller": None,
                "termination_reason": "main_worker_wall_time_exhausted",
            }
            (attempt / "solve_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (attempt / "recovery_checkpoint.json").write_text(
                json.dumps({"safe_to_resume": True}), encoding="utf-8"
            )

            self.assertFalse(
                classify_infrastructure_validity(
                    "runtime_exceeded", "main_worker_wall_time_exhausted"
                )["valid"]
            )
            allowed, source, reason = panel._retry_decision(
                root,
                replace(plan.execution, max_other_infrastructure_attempts=10),
                plan,
                job,
            )
            self.assertTrue(allowed)
            self.assertEqual(source, attempt)
            self.assertEqual(reason, "safe_interrupted_checkpoint")

    def test_control_channel_wall_time_exhaustion_is_countable(self) -> None:
        reason = "control_channel_wall_time_exhausted"

        validity = classify_infrastructure_validity(
            "control_channel_budget_exhausted", reason
        )

        self.assertTrue(validity["valid"])
        self.assertNotIn(reason, panel.RESUMABLE_TERMINAL_INFRASTRUCTURE_FAILURES)

    def test_evaluator_pass_does_not_override_protocol_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, plan = self._fixture(root)
            job = panel.Job("caseA", "qwen-control", 0)
            attempt = root / "attempt-001"
            attempt.mkdir()
            (attempt / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "arm": "controlled",
                        "seed": 0,
                        "status": "invalid_contract",
                        "termination_reason": "invalid_contract",
                        "worker": {"model": "qwen-worker"},
                        "controller": {
                            "model": "qwen-controller",
                            "transport": "http",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(
                panel,
                "validate_run_dir",
                return_value={
                    "ok": True,
                    "infrastructure_valid": True,
                    "task_success_at_budget": True,
                },
            ):
                valid = panel.valid_attempt(attempt, plan, job)
            self.assertIs(valid["protocol_valid"], False)
            self.assertIs(valid["task_passed"], False)

    def test_scheduler_only_failure_consumes_bounded_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, plan = self._fixture(root)
            (root / "attempt-001.scheduler.json").write_text("{}", encoding="utf-8")
            self.assertEqual(panel.next_attempt(root).name, "attempt-002")
            allowed, _, reason = panel._retry_decision(
                root, plan.execution, plan, panel.Job("caseA", "no-control", 0)
            )
            self.assertFalse(allowed)
            self.assertEqual(reason, "infrastructure_attempt_limit")

    def test_outer_attempts_rotate_authorized_gateway_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, plan = self._fixture(Path(temporary))
            job = panel.Job("caseA", "qwen-control", 0)
            first, first_fallback = panel._profile_pair(plan, job, 1)
            second, second_fallback = panel._profile_pair(plan, job, 2)
            self.assertEqual(first_fallback, second)
            self.assertEqual(second_fallback, first)

    def test_operational_overrides_reuse_panel_but_semantic_changes_do_not(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, plan = self._fixture(root)
            panel_root = root / "panel"
            original = plan.public_dict()
            original.update(
                {
                    "harness_git_head": "abc",
                    "harness_worktree_dirty": False,
                    "planned_run_count": len(panel.jobs_for_plan(plan)),
                }
            )
            panel._materialize_plan(panel_root, original)
            operational_change = json.loads(json.dumps(original))
            operational_change["source_plan"] = "/moved/plan.json"
            operational_change["execution"]["concurrency"] = 6
            operational_change["execution"]["max_provider_attempts"] = 5
            panel._materialize_plan(panel_root, operational_change)
            history = (
                (panel_root / "orchestration_history.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertEqual(len(history), 2)
            semantic_change = json.loads(json.dumps(original))
            semantic_change["worker"]["model"] = "different-worker"
            with self.assertRaises(RuntimeError):
                panel._materialize_plan(panel_root, semantic_change)

    def test_panel_writer_lease_rejects_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            panel_root = Path(temporary)
            self.assertFalse(panel.panel_writer_active(panel_root))
            lease = panel.acquire_panel_writer_lease(panel_root)
            try:
                self.assertTrue(panel.panel_writer_active(panel_root))
                with self.assertRaises(panel.PanelWriterActiveError):
                    panel.acquire_panel_writer_lease(panel_root)
            finally:
                lease.close()
            self.assertFalse(panel.panel_writer_active(panel_root))

    def test_shutdown_terminates_the_entire_job_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child_pid_path = root / "child.pid"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib, subprocess, sys, time; "
                        "child=subprocess.Popen([sys.executable, '-c', "
                        "'import time; time.sleep(60)']); "
                        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
                        "time.sleep(60)"
                    ),
                ],
                start_new_session=True,
            )
            deadline = time.monotonic() + 5
            while not child_pid_path.is_file() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(child_pid_path.is_file())
            with mock.patch.object(panel, "PROCESS_GROUP_GRACE_SEC", 2.0):
                panel._terminate_process_group(process)
            process.wait(timeout=5)
            self.assertFalse(panel._process_group_exists(process.pid))

    def test_summary_does_not_turn_infrastructure_gap_into_model_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, full_plan = self._fixture(root)
            plan = panel.PanelPlan(
                source=full_plan.source,
                cohort=full_plan.cohort,
                case_ids=("caseA",),
                selection_stage={"caseA": "middle"},
                seeds=(0,),
                worker_model=full_plan.worker_model,
                profiles=full_plan.profiles,
                conditions=(
                    full_plan.condition("no-control"),
                    full_plan.condition("qwen-control"),
                ),
                execution=full_plan.execution,
                baseline_condition_id="no-control",
                canary=None,
            )
            panel_root = root / "results-root"
            panel_root.mkdir()
            (panel_root / "resolved_plan.json").write_text(
                json.dumps(plan.public_dict()), encoding="utf-8"
            )
            no_control = (
                panel.job_root(panel_root, panel.Job("caseA", "no-control", 0))
                / "attempt-001"
            )
            no_control.mkdir(parents=True)
            (no_control / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "arm": "no-control",
                        "seed": 0,
                        "status": "completed",
                        "termination_reason": "natural_completion",
                        "worker": {"model": "qwen-worker"},
                        "controller": None,
                        "main_worker_turns": 4,
                        "reporter_turns": 0,
                        "controller_calls": 0,
                        "compute_accounting": {},
                    }
                ),
                encoding="utf-8",
            )
            invalid = (
                panel.job_root(panel_root, panel.Job("caseA", "qwen-control", 0))
                / "attempt-001"
            )
            invalid.mkdir(parents=True)

            def validation(path: Path, **_: object) -> dict[str, object]:
                if Path(path).resolve() == no_control.resolve():
                    return {
                        "ok": True,
                        "infrastructure_valid": True,
                        "task_success_at_budget": False,
                    }
                return {"ok": False, "infrastructure_valid": False}

            with mock.patch.object(panel, "validate_run_dir", side_effect=validation):
                summary = panel_summary.summarize(panel_root)
            baseline = summary["conditions"]["no-control"]
            controlled = summary["conditions"]["qwen-control"]
            self.assertEqual(baseline["strict_success_rate_pct"], 0.0)
            self.assertIsNone(controlled["strict_success_rate_pct"])
            self.assertIsNone(controlled["observed_valid_success_rate_pct"])
            self.assertEqual(controlled["infrastructure_invalid"], 1)
            self.assertEqual(summary["all_condition_common_valid"]["slot_count"], 0)

    def test_token_summary_fails_closed_on_partial_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            attempt = Path(temporary)
            manifest = {
                "arm": "controlled",
                "compute_accounting": {
                    "main_worker": {
                        "tokens": {
                            "request_count": 2,
                            "usage_reported_request_count": 1,
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
            (attempt / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            usage = panel_summary._manifest_usage(attempt)
            self.assertIsNone(usage["reported_total_tokens"])
            manifest["compute_accounting"]["main_worker"]["tokens"].update(
                {"usage_reported_request_count": 2}
            )
            (attempt / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            usage = panel_summary._manifest_usage(attempt)
            self.assertEqual(usage["reported_total_tokens"], 120)

    def test_summary_distinguishes_running_and_resumable_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, full_plan = self._fixture(root)
            plan = panel.PanelPlan(
                source=full_plan.source,
                cohort=full_plan.cohort,
                case_ids=("caseA",),
                selection_stage={"caseA": "middle"},
                seeds=(0,),
                worker_model=full_plan.worker_model,
                profiles=full_plan.profiles,
                conditions=(full_plan.condition("no-control"),),
                execution=full_plan.execution,
                baseline_condition_id="no-control",
                canary=None,
            )
            panel_root = root / "panel"
            panel_root.mkdir()
            (panel_root / "resolved_plan.json").write_text(
                json.dumps(plan.public_dict()), encoding="utf-8"
            )
            attempt = (
                panel.job_root(panel_root, panel.Job("caseA", "no-control", 0))
                / "attempt-001"
            )
            attempt.mkdir(parents=True)
            (attempt.parent / "attempt-001.workspace").mkdir()
            (attempt / "attempt_state.json").write_text(
                json.dumps({"status": "running", "pid": os.getpid()}),
                encoding="utf-8",
            )
            running = panel_summary.summarize(panel_root)
            self.assertEqual(
                running["case_matrix"][0]["conditions"]["no-control"]["state"],
                "running",
            )
            self.assertEqual(running["conditions"]["no-control"]["running"], 1)

            (attempt / "attempt_state.json").write_text(
                json.dumps({"status": "interrupted", "pid": 99999999}),
                encoding="utf-8",
            )
            (attempt / "recovery_checkpoint.json").write_text(
                json.dumps({"safe_to_resume": True}), encoding="utf-8"
            )
            resumable = panel_summary.summarize(panel_root)
            self.assertEqual(
                resumable["case_matrix"][0]["conditions"]["no-control"]["state"],
                "interrupted_resumable",
            )
            self.assertEqual(
                resumable["conditions"]["no-control"]["interrupted_resumable"],
                1,
            )
            self.assertEqual(
                resumable["conditions"]["no-control"]["infrastructure_invalid"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
