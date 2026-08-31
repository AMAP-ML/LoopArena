"""Deterministic Controller used for the fixed-control ablation."""

from __future__ import annotations

import json
from typing import Any

POLICY_ID = "looparena.non_adaptive_fixed_goal"
PROVIDER_KIND = "non-adaptive-fixed"
COMPLETION_SIGNAL = "Goal complete."

FIXED_GOAL = "Complete the original repository task."
FIXED_INSTRUCTION = (
    "Review the current state, decide what remains, and continue with the most "
    "useful next actions."
)
FIXED_COMPLETION_CONDITION = (
    "After taking the most useful next actions, hand back normally. If you have "
    "verified that the original repository task is complete, end your response "
    "with the exact final line:\n"
    f"{COMPLETION_SIGNAL}\n"
    "Otherwise, do not use that line."
)
FIXED_VERIFICATION_CONDITION = (
    "Inspect the current repository state and take the most useful next actions "
    "toward the original task."
)


class NonAdaptiveFixedController:
    """Repeat one generic Contract without inspecting Packet contents."""

    model = POLICY_ID
    policy_id = POLICY_ID
    provider_kind = PROVIDER_KIND
    transport = "local-deterministic"
    base_url = ""
    credential_profile_id = "none"
    max_retries = 0

    def __init__(self) -> None:
        self.packet_bound = False
        self.current_cycle_index: int | None = None
        self.previous_worker_events_bound = False
        self.previous_worker_declared_complete = False
        # This provider makes no model calls.  Keeping this empty prevents its
        # deterministic decisions from being counted as model token usage.
        self.call_audits: list[dict[str, Any]] = []

    def set_current_packet(self, packet: dict[str, Any]) -> None:
        if not isinstance(packet, dict):
            raise TypeError("non-adaptive fixed control requires a Packet object")
        # Bind the standard Packet interface without reading or retaining any
        # Packet field.  The boolean is enough to enforce the runtime contract.
        self.packet_bound = True

    def set_current_cycle_index(self, cycle_index: int) -> None:
        self.current_cycle_index = int(cycle_index)

    def set_previous_worker_tool_events(
        self,
        tool_events: list[dict[str, Any]],
    ) -> None:
        if not isinstance(tool_events, list) or any(
            not isinstance(event, dict) for event in tool_events
        ):
            raise TypeError("tool_events must be a list of objects")
        self.previous_worker_events_bound = True
        boundary_texts = [
            str(event.get("result") or "")
            for event in tool_events
            if event.get("event_kind") == "assistant_boundary"
        ]
        last_nonempty_line = ""
        if boundary_texts:
            lines = [
                line.strip() for line in boundary_texts[-1].splitlines() if line.strip()
            ]
            if lines:
                last_nonempty_line = lines[-1]
        self.previous_worker_declared_complete = last_nonempty_line == COMPLETION_SIGNAL

    @staticmethod
    def _advance_response() -> dict[str, Any]:
        return {
            "action": "advance",
            "rationale": "Continue the fixed goal.",
            "worker_instruction": {
                "goal": FIXED_GOAL,
                "context": FIXED_INSTRUCTION,
                "required_outcomes": [],
                "prohibited_actions": [],
                "completion_condition": FIXED_COMPLETION_CONDITION,
            },
            "protected_invariants": [],
            "verification_acceptance_condition": FIXED_VERIFICATION_CONDITION,
        }

    @staticmethod
    def _stop_response() -> dict[str, Any]:
        return {
            "action": "stop",
            "rationale": "The Worker explicitly declared the fixed goal complete.",
            "worker_instruction": None,
            "protected_invariants": [],
            "verification_acceptance_condition": "",
        }

    def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> str:
        del messages, max_tokens
        if not self.packet_bound:
            raise ValueError("non-adaptive fixed control has no bound Packet")
        if self.current_cycle_index is None:
            raise ValueError("non-adaptive fixed control has no bound control cycle")
        if self.current_cycle_index < 0:
            raise ValueError("non-adaptive fixed control cycle must be non-negative")
        if not self.previous_worker_events_bound:
            raise ValueError("non-adaptive fixed control has no bound Worker boundary")
        response = (
            self._stop_response()
            if self.current_cycle_index > 1 and self.previous_worker_declared_complete
            else self._advance_response()
        )
        return json.dumps(response, ensure_ascii=False, sort_keys=True)


__all__ = [
    "NonAdaptiveFixedController",
    "COMPLETION_SIGNAL",
    "FIXED_COMPLETION_CONDITION",
    "FIXED_GOAL",
    "FIXED_INSTRUCTION",
    "FIXED_VERIFICATION_CONDITION",
    "POLICY_ID",
    "PROVIDER_KIND",
]
