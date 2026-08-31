"""Minimal runtime checks for model handoffs.

These checks protect execution, not a frozen authoring format.
"""

from __future__ import annotations

import json
from typing import Any

from . import protocol as P

CONTROLLER_INPUT_MAX_CHARACTERS = 800_000
CONTROLLER_EVIDENCE_SEGMENT_MAX_CHARACTERS = 32_768
ROUND_REPORT_FIELDS = (
    "task_context_and_constraints",
    "work_history_and_current_state",
    "verification_and_evidence",
    "open_issues_and_uncertainty",
)


def capacity_policy_identity() -> dict[str, Any]:
    """Describe the deterministic Controller-visible context policy."""

    return {
        "measurement": "canonical_messages_json_characters",
        "controller_input_max_characters": CONTROLLER_INPUT_MAX_CHARACTERS,
        "evidence_segment_max_characters": CONTROLLER_EVIDENCE_SEGMENT_MAX_CHARACTERS,
    }


def packet_capacity_errors(packet: object) -> list[str]:
    """Reject only payloads that cannot be transported as JSON.

    The harness must not invent byte, card, or history limits independent of the
    selected model's context window. In particular, it never silently clips a
    task, report, packet, or rendered prompt.
    """

    if not isinstance(packet, dict):
        return ["packet_must_be_object"]
    try:
        json.dumps(packet, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return ["packet_must_be_json_serializable"]
    return []


def require_packet_capacity(packet: object) -> None:
    errors = packet_capacity_errors(packet)
    if errors:
        raise ValueError(";".join(errors))


def require_rendered_packet_prompt_capacity(text: str) -> None:
    if not isinstance(text, str):
        raise ValueError("rendered prompt must be a string")


def require_task_text(value: Any) -> str:
    """Return an unchanged non-empty task."""

    if not isinstance(value, str):
        raise ValueError("task text must be a string")
    if not value.strip():
        raise ValueError("task text must not be empty")
    return value


def _dedupe(errors: list[str]) -> list[str]:
    return list(dict.fromkeys(errors))


def validate_public_conversation(messages: object) -> list[str]:
    """Check only that a model conversation can be replayed."""

    if not isinstance(messages, list) or not messages:
        return ["public_conversation_must_be_nonempty_list"]
    errors: list[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            errors.append(f"public_conversation_message[{index}]_must_be_object")
            continue
        role = message.get("role")
        if not isinstance(role, str) or not role.strip():
            errors.append(f"public_conversation_message[{index}]_role_invalid")
        if "content" not in message and "tool_calls" not in message:
            errors.append(f"public_conversation_message[{index}]_payload_missing")
        if "tool_calls" in message and not isinstance(message.get("tool_calls"), list):
            errors.append(
                f"public_conversation_message[{index}]_tool_calls_must_be_list"
            )
    return errors


def validate_packet_budget(value: Any, *, require_canonical: bool) -> list[str]:
    if not isinstance(value, dict):
        return ["packet_budget_must_be_object"]
    canonical = (
        "max_inner_react_turns_total",
        "used_inner_react_turns",
        "remaining_inner_react_turns",
    )
    if not require_canonical and not any(field in value for field in canonical):
        return []
    errors: list[str] = []
    for field in canonical:
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            errors.append(f"packet_budget_{field}_invalid")
    total, used, remaining = (value.get(field) for field in canonical)
    if all(
        isinstance(item, int) and not isinstance(item, bool)
        for item in (total, used, remaining)
    ):
        if used + remaining != total:
            errors.append("packet_budget_accounting_mismatch")
    return errors


def validate_packet(packet: dict) -> list[str]:
    """Check the information that the controller actually needs."""

    if not isinstance(packet, dict):
        return ["packet_must_be_object"]
    errors = packet_capacity_errors(packet)
    sample_id = packet.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id.strip():
        errors.append("sample_id_must_be_nonempty_string")
    try:
        require_task_text(packet.get("task"))
    except ValueError as exc:
        errors.append(f"packet_task_text:{exc}")

    if "round_report" in packet:
        report = packet.get("round_report")
        if not isinstance(report, dict):
            errors.append("round_report_must_be_object")
        else:
            for field in ROUND_REPORT_FIELDS:
                value = report.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"round_report_{field}_must_be_nonempty_string")
        errors.extend(
            validate_packet_budget(packet.get("budget"), require_canonical=True)
        )
    elif "budget" in packet:
        errors.extend(
            validate_packet_budget(packet.get("budget"), require_canonical=False)
        )

    actions = packet.get("allowed_actions", packet.get("allowed_control_decisions"))
    if actions is not None:
        if not isinstance(actions, list) or any(
            action not in P.CONTROL_DECISIONS for action in actions
        ):
            errors.append("packet_allowed_actions_invalid")
    return _dedupe(errors)


def _submitted_action(contract: dict[str, Any]) -> object:
    if "action" in contract:
        return contract.get("action")
    decision = contract.get("control_decision")
    return decision.get("action") if isinstance(decision, dict) else None


def validate_contract(contract: dict, packet: dict) -> list[str]:
    """Check only what is needed to stop or to dispatch actionable work."""

    errors = validate_packet(packet)
    if not isinstance(contract, dict):
        return _dedupe([*errors, "contract_must_be_object"])
    action = _submitted_action(contract)
    if action == "stop":
        return _dedupe(errors)
    if action not in {"advance", "verify"}:
        errors.append("contract_action_must_be_advance_verify_or_stop")
        return _dedupe(errors)

    sample_id = contract.get("sample_id")
    if sample_id not in (None, "", packet.get("sample_id")):
        errors.append("contract_sample_id_mismatch")

    instruction = contract.get("worker_instruction")
    if not isinstance(instruction, dict):
        instruction = contract.get("control_instruction")
    if not isinstance(instruction, dict):
        errors.append("contract_worker_instruction_must_be_object")
        return _dedupe(errors)
    goal = instruction.get("goal")
    completion = instruction.get("completion_condition")
    if not isinstance(goal, str) or not goal.strip():
        errors.append("contract_worker_instruction_goal_missing")
    if not isinstance(completion, str) or not completion.strip():
        errors.append("contract_worker_instruction_completion_condition_missing")
    return _dedupe(errors)
