from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from looparena.harness.controller import (
    extract_json_object,
    normalize_controller_contract,
)


def _continue_contract() -> dict:
    return {
        "action": "advance",
        "rationale": "More work is required.",
        "worker_instruction": {
            "goal": "Finish the implementation.",
            "context": "Use the reported evidence.",
            "required_outcomes": ["The focused behavior works."],
            "prohibited_actions": ["Do not inspect private tests."],
            "completion_condition": "Pause after focused verification.",
        },
        "protected_invariants": ["Existing behavior remains intact."],
        "verification_acceptance_condition": "The focused check passes.",
    }


class ControllerContractTest(unittest.TestCase):
    def test_valid_response_is_mapped_without_repair(self) -> None:
        visible = _continue_contract()
        parsed = extract_json_object(json.dumps(visible))
        contract = normalize_controller_contract(parsed, {"sample_id": "case"})
        self.assertEqual(contract["control_decision"]["action"], "advance")
        self.assertEqual(
            contract["control_instruction"]["goal"],
            "Finish the implementation.",
        )

    def test_stop_uses_empty_assignment_fields(self) -> None:
        visible = {
            "action": "stop",
            "rationale": "The reported evidence establishes completion.",
            "worker_instruction": None,
            "protected_invariants": [],
            "verification_acceptance_condition": "",
        }
        contract = normalize_controller_contract(visible, {"sample_id": "case"})
        self.assertEqual(contract["control_decision"]["action"], "stop")

    def test_trailing_comma_is_invalid(self) -> None:
        malformed = json.dumps(_continue_contract()).replace(
            '"completion_condition": "Pause after focused verification."}',
            '"completion_condition": "Pause after focused verification.",}',
        )
        with self.assertRaises(json.JSONDecodeError):
            extract_json_object(malformed)

    def test_truncated_json_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            extract_json_object('{"action":"advance"')

    def test_misnested_fields_are_invalid(self) -> None:
        visible = _continue_contract()
        visible["worker_instruction"]["protected_invariants"] = visible.pop(
            "protected_invariants"
        )
        with self.assertRaises(ValueError):
            normalize_controller_contract(visible, {"sample_id": "case"})


if __name__ == "__main__":
    unittest.main()
