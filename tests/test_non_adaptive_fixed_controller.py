from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from looparena.harness.continuous_session import ContinuousHarnessSession
from looparena.harness.controller import (
    bind_controller_cycle,
    bind_controller_packet,
    bind_controller_worker_activity,
    run_controller,
)
from looparena.harness.rendering import render_controlled_continuation
from looparena.runtime.non_adaptive_fixed_controller import (
    COMPLETION_SIGNAL,
    FIXED_COMPLETION_CONDITION,
    FIXED_GOAL,
    FIXED_INSTRUCTION,
    NonAdaptiveFixedController,
)
from looparena.runtime.worker_session import run_main_worker_until_boundary


def _packet(*, sample_id: str, report: str) -> dict:
    return {
        "sample_id": sample_id,
        "task": "Fix the parser and run the relevant tests.",
        "test_note": report,
        "allowed_control_decisions": ["advance", "verify", "stop"],
    }


def _boundary(text: str) -> dict:
    return {
        "name": "inner_loop_completion",
        "event_kind": "assistant_boundary",
        "result": text,
        "executed": False,
        "tool_error": False,
    }


def _bind(
    controller: NonAdaptiveFixedController,
    packet: dict,
    *,
    cycle: int,
    repo_tool_calls: int,
    boundary_text: str = "Useful work remains.",
) -> None:
    bind_controller_packet(controller, packet)
    bind_controller_cycle(controller, cycle)
    bind_controller_worker_activity(
        controller,
        repo_tool_calls,
        [_boundary(boundary_text)],
    )


class NonAdaptiveFixedControllerTest(unittest.TestCase):
    def test_first_control_cycle_issues_the_minimal_fixed_contract(self) -> None:
        controller = NonAdaptiveFixedController()
        packet = _packet(sample_id="case-a", report="The task may be unfinished.")
        _bind(controller, packet, cycle=1, repo_tool_calls=0)

        result = run_controller(packet, controller)

        self.assertEqual(result.validation_errors, [])
        self.assertEqual(result.contract["control_decision"]["action"], "advance")
        instruction = result.contract["control_instruction"]
        self.assertEqual(instruction["goal"], FIXED_GOAL)
        self.assertEqual(instruction["notes_for_worker"], [FIXED_INSTRUCTION])
        self.assertEqual(instruction["required_actions"], [])
        self.assertEqual(instruction["disallowed_actions"], [])
        self.assertEqual(
            instruction["completion_condition"],
            FIXED_COMPLETION_CONDITION,
        )
        self.assertEqual(result.contract["protected_invariants"], [])
        self.assertEqual(result.model_call_audits, [])
        self.assertEqual(controller.call_audits, [])
        self.assertTrue(result.messages)

    def test_packet_content_cannot_change_an_advance_decision(self) -> None:
        responses = []
        for report in ("Everything is complete.", "Nothing has been attempted."):
            controller = NonAdaptiveFixedController()
            packet = _packet(sample_id="same-case", report=report)
            _bind(controller, packet, cycle=2, repo_tool_calls=1)
            responses.append(controller([], max_tokens=1))

        self.assertEqual(responses[0], responses[1])

    def test_each_active_cycle_repeats_the_exact_same_contract(self) -> None:
        packet = _packet(sample_id="same-case", report="Ignored by policy.")
        responses = []
        for cycle in (1, 2, 9):
            controller = NonAdaptiveFixedController()
            _bind(controller, packet, cycle=cycle, repo_tool_calls=4)
            responses.append(controller([]))

        self.assertEqual(responses[0], responses[1])
        self.assertEqual(responses[1], responses[2])

    def test_read_only_cycle_without_completion_signal_keeps_running(self) -> None:
        controller = NonAdaptiveFixedController()
        packet = _packet(sample_id="case-b", report="Ignored by policy.")
        _bind(
            controller,
            packet,
            cycle=2,
            repo_tool_calls=2,
            boundary_text="Inspection found another unresolved path.",
        )

        result = run_controller(packet, controller)

        self.assertEqual(result.validation_errors, [])
        self.assertEqual(result.contract["control_decision"]["action"], "advance")

    def test_exact_final_line_completion_signal_stops(self) -> None:
        controller = NonAdaptiveFixedController()
        packet = _packet(sample_id="case-b", report="Ignored by policy.")
        _bind(
            controller,
            packet,
            cycle=2,
            repo_tool_calls=5,
            boundary_text=f"Tests pass.\n\n{COMPLETION_SIGNAL}",
        )

        result = run_controller(packet, controller)

        self.assertEqual(result.validation_errors, [])
        self.assertEqual(result.contract["control_decision"]["action"], "stop")
        self.assertNotIn("control_instruction", result.contract)
        raw = json.loads(result.raw_response)
        self.assertIsNone(raw["worker_instruction"])

    def test_completion_signal_is_ignored_when_not_the_final_line(self) -> None:
        controller = NonAdaptiveFixedController()
        packet = _packet(sample_id="case-b", report="Ignored by policy.")
        _bind(
            controller,
            packet,
            cycle=2,
            repo_tool_calls=0,
            boundary_text=f"{COMPLETION_SIGNAL}\nOne issue still remains.",
        )

        result = run_controller(packet, controller)

        self.assertEqual(result.contract["control_decision"]["action"], "advance")

    def test_bootstrap_boundary_cannot_stop_the_first_control_cycle(self) -> None:
        controller = NonAdaptiveFixedController()
        packet = _packet(sample_id="case-b", report="Ignored by policy.")
        _bind(
            controller,
            packet,
            cycle=1,
            repo_tool_calls=0,
            boundary_text=COMPLETION_SIGNAL,
        )

        result = run_controller(packet, controller)

        self.assertEqual(result.contract["control_decision"]["action"], "advance")

    def test_provider_requires_all_runtime_bindings(self) -> None:
        controller = NonAdaptiveFixedController()
        with self.assertRaisesRegex(ValueError, "no bound Packet"):
            controller([])

        bind_controller_packet(
            controller,
            _packet(sample_id="case-c", report="Any report."),
        )
        with self.assertRaisesRegex(ValueError, "no bound control cycle"):
            controller([])

        bind_controller_cycle(controller, 1)
        with self.assertRaisesRegex(ValueError, "no bound Worker boundary"):
            controller([])

        with self.assertRaisesRegex(TypeError, "list of objects"):
            controller.set_previous_worker_tool_events(["not-an-event"])  # type: ignore[list-item]

    def test_session_uses_the_standard_controlled_renderer(self) -> None:
        class ConversationStub:
            def __init__(self) -> None:
                self.messages: list[dict] = []
                self.appended: list[str] = []
                self.total_model_calls = 0

            def append_user_turn(self, content: str) -> None:
                self.appended.append(content)

        packet = _packet(sample_id="session-case", report="Ignored by policy.")
        conversation = ConversationStub()
        controller = NonAdaptiveFixedController()
        session = ContinuousHarnessSession(
            arm="controlled",
            start_context=packet,
            initial_control_packet=packet,
            conversation=conversation,  # type: ignore[arg-type]
            controller=controller,
            reporter_sandbox_factory=lambda: object(),
        )
        session.main_worker_slices.append(
            {
                "repo_tool_calls": 3,
                "tool_events": [
                    {"name": "str_replace", "result": "edited"},
                    _boundary("Useful work remains."),
                ],
            }
        )

        applied = session._apply_contract(packet, 1, packet_generated=True)

        self.assertTrue(applied)
        self.assertEqual(len(session.controller_results), 1)
        old_reporter_packet = {
            **packet,
            "budget": {"remaining_inner_react_turns": 600},
        }
        expected = render_controlled_continuation(
            session.controller_results[0].contract,
            old_reporter_packet,
        )
        self.assertEqual(conversation.appended, [expected])
        self.assertFalse(hasattr(controller, "render_worker_continuation"))
        self.assertNotIn("LOOPARENA_GOAL_STATUS", expected)
        self.assertNotIn("update_goal", expected)

    def test_pending_controller_result_resumes_from_canonical_conversation(
        self,
    ) -> None:
        class ConversationStub:
            def __init__(self) -> None:
                self.messages: list[dict] = []
                self.appended: list[str] = []
                self.total_model_calls = 0

            def append_user_turn(self, content: str) -> None:
                self.appended.append(content)

        packet = _packet(sample_id="resume-case", report="Ignored by policy.")
        controller = NonAdaptiveFixedController()
        _bind(controller, packet, cycle=1, repo_tool_calls=0)
        result = run_controller(packet, controller)
        conversation = ConversationStub()
        session = ContinuousHarnessSession(
            arm="controlled",
            start_context=packet,
            initial_control_packet=packet,
            conversation=conversation,  # type: ignore[arg-type]
            controller=controller,
            reporter_sandbox_factory=lambda: object(),
        )
        session.controller_results.append(result)

        applied = session._apply_contract(
            packet,
            1,
            packet_generated=True,
            pending_controller_state={
                "result": session._controller_result_row(result),
                "messages": result.messages,
                "raw_response": result.raw_response,
            },
        )

        self.assertTrue(applied)
        self.assertEqual(len(conversation.appended), 1)
        self.assertEqual(
            session._controller_conversation,
            [
                *result.messages,
                {"role": "assistant", "content": result.raw_response},
            ],
        )

    def test_session_binds_only_the_previous_main_worker_slice_activity(self) -> None:
        class ConversationStub:
            messages: list[dict] = []

            def append_user_turn(self, content: str) -> None:
                raise AssertionError(f"Stop must not append a Worker turn: {content}")

        packet = _packet(sample_id="stop-case", report="Ignored by policy.")
        session = ContinuousHarnessSession(
            arm="controlled",
            start_context=packet,
            initial_control_packet=packet,
            conversation=ConversationStub(),  # type: ignore[arg-type]
            controller=NonAdaptiveFixedController(),
            reporter_sandbox_factory=lambda: object(),
        )
        session.main_worker_slices.extend(
            [
                {
                    "repo_tool_calls": 7,
                    "tool_events": [{"name": "create", "result": "created file.py"}],
                },
                {
                    "repo_tool_calls": 2,
                    "tool_events": [
                        {"name": "run_command", "result": "[exit 0] tests pass"},
                        _boundary(f"Verified.\n{COMPLETION_SIGNAL}"),
                    ],
                },
            ]
        )

        applied = session._apply_contract(packet, 2, packet_generated=True)

        self.assertFalse(applied)
        self.assertEqual(session.status, "completed")
        self.assertEqual(session.termination_reason, "controller_stop")
        self.assertEqual(session.terminal_action, "stop")

    def test_controlled_worker_natural_response_still_hands_back(self) -> None:
        class WorkerStub:
            def chat(self, messages, tools, *, seed):
                del messages, tools, seed
                return {
                    "role": "assistant",
                    "content": "Current cycle complete.",
                    "tool_calls": [],
                }

        result = run_main_worker_until_boundary(
            WorkerStub(),
            object(),
            Path("."),
            [{"role": "user", "content": "Continue the task."}],
            arm="controlled",
            turns_remaining=3,
        )

        self.assertEqual(result["status"], "inner_loop_completed")
        self.assertEqual(
            result["termination_reason"],
            "controlled_inner_loop_natural_completion",
        )
        self.assertNotIn("completion_scope", result)

    def test_full_fixed_loop_repeats_then_stops_after_verification_cycle(self) -> None:
        packet = _packet(sample_id="loop-case", report="Ignored by policy.")
        first_controller = NonAdaptiveFixedController()
        _bind(first_controller, packet, cycle=1, repo_tool_calls=2)
        first_result = run_controller(packet, first_controller)

        class ConversationStub:
            def __init__(self) -> None:
                self.messages = [{"role": "user", "content": "Fixed cycle one."}]
                self.total_model_calls = 1
                self.total_repo_actions = 2
                self.appended: list[str] = []
                self.slice_activity = [
                    (
                        1,
                        [
                            {"name": "str_replace", "result": "edited"},
                            _boundary("Implementation changed; verify it next."),
                        ],
                    ),
                    (
                        2,
                        [
                            {"name": "run_command", "result": "[exit 0] tests pass"},
                            _boundary(f"Verified.\n{COMPLETION_SIGNAL}"),
                        ],
                    ),
                ]

            def append_user_turn(self, content: str) -> None:
                self.appended.append(content)
                self.messages.append({"role": "user", "content": content})

            def run_main_until_boundary(self, **kwargs):
                repo_calls, tool_events = self.slice_activity.pop(0)
                self.total_model_calls += 1
                self.total_repo_actions += repo_calls
                self.messages.append(
                    {"role": "assistant", "content": "Current cycle complete."}
                )
                return {
                    "arm": kwargs["arm"],
                    "control_decision": kwargs["control_decision"],
                    "status": "inner_loop_completed",
                    "termination_reason": "controlled_inner_loop_natural_completion",
                    "message_range": [len(self.messages) - 1, len(self.messages)],
                    "main_worker_turns": 1,
                    "repo_tool_calls": repo_calls,
                    "tool_events": tool_events,
                    "model_response_audit": [],
                }

        pending = {
            "cycle_index": 1,
            "next_action": "call_worker",
            "current_packet": packet,
            "main_worker_slices": [
                {
                    "repo_tool_calls": 2,
                    "tool_events": [
                        {"name": "create", "result": "created file.py"},
                        _boundary("Bootstrap complete."),
                    ],
                }
            ],
            "controller_results": [
                ContinuousHarnessSession._controller_result_row(first_result)
            ],
            "current_worker_action": "advance",
            "active_contract": first_result.contract,
        }
        conversation = ConversationStub()

        def reporter_must_not_run() -> object:
            raise AssertionError("fixed control must not invoke the Reporter")

        session = ContinuousHarnessSession(
            arm="controlled",
            start_context=packet,
            initial_control_packet=packet,
            conversation=conversation,  # type: ignore[arg-type]
            controller=NonAdaptiveFixedController(),
            reporter_sandbox_factory=reporter_must_not_run,
            resume_state=pending,
        )

        result = session.run(
            resume_existing_conversation=True,
            resume_in_progress=True,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.termination_reason, "controller_stop")
        self.assertEqual(result.terminal_action, "stop")
        self.assertEqual(result.controller_calls, 3)
        self.assertEqual(
            [
                row.contract["control_decision"]["action"]
                for row in result.controller_results
            ],
            ["advance", "advance", "stop"],
        )
        self.assertEqual(len(conversation.appended), 1)
        self.assertIn("- model responses remaining: 598", conversation.appended[0])
        self.assertEqual(conversation.slice_activity, [])
        self.assertEqual(result.reporter_runs, [])
        self.assertEqual(result.reporter_turns, 0)
        self.assertEqual(result.packets, [])

    def test_provider_identity_has_no_model_sampling_or_credentials(self) -> None:
        controller = NonAdaptiveFixedController()
        self.assertEqual(controller.provider_kind, "non-adaptive-fixed")
        self.assertEqual(controller.transport, "local-deterministic")
        self.assertEqual(controller.credential_profile_id, "none")
        self.assertEqual(controller.max_retries, 0)
        self.assertEqual(
            controller.policy_id,
            "looparena.non_adaptive_fixed_goal",
        )


if __name__ == "__main__":
    unittest.main()
