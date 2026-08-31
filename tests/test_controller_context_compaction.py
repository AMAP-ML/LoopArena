from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from looparena.harness import protocol as P
from looparena.harness import rendering as R
from looparena.harness import validation as V
from looparena.harness.controller import (
    CONTROLLER_MAX_OUTPUT_TOKENS,
    controller_messages,
    prepare_controller_messages,
    run_controller,
)
from looparena.harness.evaluator_protocol import classify_infrastructure_validity
from looparena.runtime.controller_client import ControllerCallError


def _packet(
    *,
    evidence_result: str = "focused check passed",
    report_text: str = "The work remains in progress. [E1]",
) -> dict:
    return {
        "sample_id": "context-test",
        "task": "Implement and verify the requested repository change.",
        "round_report": {
            "task_context_and_constraints": report_text,
            "work_history_and_current_state": "The coding agent inspected the code. [E1]",
            "verification_and_evidence": "The recorded check is quoted. [E1]",
            "open_issues_and_uncertainty": "Completion is not established. [E1]",
        },
        "quoted_worker_evidence": [
            {
                "evidence_ref": "E1",
                "assistant_turn": 1,
                "assistant_text": "I ran the focused check.",
                "tool_interactions": [
                    {
                        "tool_name": "run_command",
                        "arguments": {"command": "python -m pytest -q"},
                        "recorded_result": evidence_result,
                        "tool_call_id": "call-1",
                    }
                ],
            }
        ],
        "budget": {
            "budget_unit": "main_worker_react_turn",
            "max_inner_react_turns_total": 600,
            "used_inner_react_turns": 1,
            "remaining_inner_react_turns": 599,
        },
        "allowed_actions": list(P.CONTROL_DECISIONS),
    }


class ControllerContextCompactionTest(unittest.TestCase):
    def test_controller_default_output_budget_is_20480(self) -> None:
        observed_max_tokens: list[int | None] = []

        def client(messages: list[dict], *, max_tokens: int | None = None) -> str:
            del messages
            observed_max_tokens.append(max_tokens)
            return '{"action":"stop","rationale":"The task is complete.","worker_instruction":null}'

        run_controller(_packet(), client)

        self.assertEqual(CONTROLLER_MAX_OUTPUT_TOKENS, 20_480)
        self.assertEqual(observed_max_tokens, [20_480])

    def test_under_limit_request_is_byte_identical_even_for_long_evidence(self) -> None:
        packet = _packet(evidence_result="x" * 100_000)
        expected = [
            {"role": "system", "content": R.CONTROLLER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": R.render_controller_packet(packet, first_turn=True),
            },
        ]

        messages, audit = prepare_controller_messages(packet)

        self.assertEqual(messages, expected)
        self.assertFalse(audit["compaction_applied"])
        self.assertFalse(audit["preflight_exhausted"])
        self.assertEqual(
            audit["original_input_characters"],
            audit["visible_input_characters"],
        )
        self.assertNotIn("<evidence_omitted", messages[-1]["content"])

    def test_overflowed_current_evidence_keeps_deterministic_head_and_tail(
        self,
    ) -> None:
        raw = "HEAD" + "a" * 410_000 + "MIDDLE_SENTINEL" + "b" * 410_000 + "TAIL"
        messages, audit = prepare_controller_messages(_packet(evidence_result=raw))

        visible = messages[-1]["content"]
        self.assertTrue(audit["compaction_applied"])
        self.assertFalse(audit["preflight_exhausted"])
        self.assertLessEqual(
            audit["visible_input_characters"],
            V.CONTROLLER_INPUT_MAX_CHARACTERS,
        )
        self.assertEqual(
            audit["effective_evidence_segment_max_characters"],
            V.CONTROLLER_EVIDENCE_SEGMENT_MAX_CHARACTERS,
        )
        self.assertEqual(len(audit["current_evidence_segments"]), 1)
        self.assertIn("HEAD", visible)
        self.assertIn("TAIL", visible)
        self.assertNotIn("MIDDLE_SENTINEL", visible)
        self.assertIn('<evidence_omitted refs="E1"', visible)

    def test_old_expanded_evidence_is_omitted_before_current_report(self) -> None:
        old_packet = _packet(evidence_result="old evidence " * 70_000)
        old_user = R.render_controller_packet(old_packet, first_turn=True)
        previous = [
            {"role": "system", "content": R.CONTROLLER_SYSTEM_PROMPT},
            {"role": "user", "content": old_user},
            {"role": "assistant", "content": '{"action":"verify"}'},
        ]
        current = _packet(evidence_result="current focused evidence")

        messages, audit = prepare_controller_messages(current, previous)

        self.assertTrue(audit["compaction_applied"])
        self.assertFalse(audit["preflight_exhausted"])
        self.assertEqual(len(audit["historical_evidence_messages"]), 1)
        self.assertIn("<historical_evidence_omitted", messages[1]["content"])
        self.assertIn("Task context and constraints", messages[1]["content"])
        self.assertIn("current focused evidence", messages[-1]["content"])
        self.assertEqual(messages[2], previous[2])

    def test_consecutive_turns_form_one_capped_segment(self) -> None:
        packet = _packet(evidence_result="a" * 410_000)
        second = copy.deepcopy(packet["quoted_worker_evidence"][0])
        second.update(
            {
                "evidence_ref": "E2",
                "assistant_turn": 2,
                "tool_call_id": "call-2",
            }
        )
        second["tool_interactions"][0]["tool_call_id"] = "call-2"
        second["tool_interactions"][0]["recorded_result"] = "b" * 410_000
        packet["quoted_worker_evidence"].append(second)

        _, audit = prepare_controller_messages(packet)

        self.assertEqual(len(audit["current_evidence_segments"]), 1)
        self.assertEqual(audit["current_evidence_segments"][0]["refs"], "E1–E2")

    def test_uncompactable_request_fails_before_provider_call(self) -> None:
        packet = _packet(report_text="r" * (V.CONTROLLER_INPUT_MAX_CHARACTERS + 10_000))
        calls = 0

        def client(messages: list[dict], *, max_tokens: int | None = None) -> str:
            nonlocal calls
            calls += 1
            raise AssertionError("provider must not be called")

        result = run_controller(packet, client)

        self.assertEqual(calls, 0)
        self.assertEqual(
            result.failure_kind,
            "controller_context_preflight_exhausted",
        )
        self.assertEqual(
            result.validation_errors,
            ["controller_context_preflight_exhausted"],
        )
        self.assertTrue(result.context_compaction_audit["preflight_exhausted"])
        self.assertFalse(
            classify_infrastructure_validity(
                "controller_context_preflight_exhausted",
                "controller_context_preflight_exhausted",
            )["valid"]
        )
        with self.assertRaisesRegex(
            RuntimeError, "controller_context_preflight_exhausted"
        ):
            controller_messages(packet)

    def test_controller_policy_violation_is_not_infrastructure(self) -> None:
        def client(messages: list[dict], *, max_tokens: int | None = None) -> str:
            del messages, max_tokens
            raise ControllerCallError(
                "Codex controller attempted forbidden item type: web_search",
                failure_kind="invalid_contract",
                error_code="codex_forbidden_item_web_search",
            )

        result = run_controller(_packet(), client)

        self.assertEqual(result.failure_kind, "invalid_contract")
        self.assertEqual(
            result.validation_errors,
            ["controller_submission_error:codex_forbidden_item_web_search"],
        )
        self.assertEqual(
            result.failure_diagnostics["error_code"],
            "codex_forbidden_item_web_search",
        )


if __name__ == "__main__":
    unittest.main()
