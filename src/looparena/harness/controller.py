"""Controller utilities for the LoopArena natural-inner-loop harness."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import protocol as P
from . import rendering as R
from . import validation as V

Message = dict[str, str]

# Keep the Controller ceiling aligned with the formal Type I configuration.
# A lower 8,192-token ceiling was observed to truncate otherwise valid Loop
# Contracts for some providers, so the release runtime uses one non-binding
# ceiling across Controller evaluations.
CONTROLLER_MAX_OUTPUT_TOKENS = 20_480


class ChatClient(Protocol):
    """Small protocol shared by API-backed models and test fakes."""

    def __call__(
        self, messages: list[Message], *, max_tokens: int | None = None
    ) -> str: ...


def bind_controller_packet(controller: ChatClient, packet: dict) -> None:
    setter = getattr(controller, "set_current_packet", None)
    if setter is not None:
        if not callable(setter):
            raise TypeError("controller.set_current_packet must be callable")
        setter(copy.deepcopy(packet))


def bind_controller_cycle(controller: ChatClient, cycle_index: int) -> None:
    setter = getattr(controller, "set_current_cycle_index", None)
    if setter is not None:
        if not callable(setter):
            raise TypeError("controller.set_current_cycle_index must be callable")
        setter(int(cycle_index))


def bind_controller_worker_activity(
    controller: ChatClient,
    repo_tool_calls: int,
    tool_events: list[dict] | None = None,
) -> None:
    setter = getattr(controller, "set_previous_worker_repo_tool_calls", None)
    if setter is not None:
        if not callable(setter):
            raise TypeError(
                "controller.set_previous_worker_repo_tool_calls must be callable"
            )
        setter(int(repo_tool_calls))
    event_setter = getattr(controller, "set_previous_worker_tool_events", None)
    if event_setter is not None and tool_events is not None:
        if not callable(event_setter):
            raise TypeError(
                "controller.set_previous_worker_tool_events must be callable"
            )
        event_setter(copy.deepcopy(tool_events))


@dataclass(frozen=True)
class ControllerResult:
    contract: dict
    messages: list[Message]
    raw_response: str
    validation_errors: list[str]
    failure_kind: str = ""
    failure_diagnostics: dict = field(default_factory=dict)
    model_call_audits: list[dict] = field(default_factory=list)
    context_compaction_audit: dict = field(default_factory=dict)


_CONTROLLER_FIELDS = {
    "action",
    "rationale",
    "worker_instruction",
    "protected_invariants",
    "verification_acceptance_condition",
}
_WORKER_INSTRUCTION_FIELDS = {
    "goal",
    "context",
    "required_outcomes",
    "prohibited_actions",
    "completion_condition",
}


def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from model output."""

    # Tolerate a harmless prose or Markdown envelope from the provider; the
    # JSON object itself still receives the normal strict contract validation.
    cleaned = re.sub(r"```(?:json)?", "", text or "")
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("model response contains no JSON object")
    value, _end = json.JSONDecoder().raw_decode(cleaned, start)
    if not isinstance(value, dict):
        raise ValueError("controller response must be a JSON object")
    return value


def normalize_controller_contract(contract: dict, packet: dict | None = None) -> dict:
    """Map the model-facing Controller response onto the worker contract."""
    if not isinstance(contract, dict):
        raise ValueError("controller response must be a JSON object")

    if set(contract) != _CONTROLLER_FIELDS:
        raise ValueError("controller response has unexpected fields")

    action = contract.get("action")
    rationale = contract.get("rationale")
    instruction = contract.get("worker_instruction")
    protected = contract.get("protected_invariants")
    acceptance = contract.get("verification_acceptance_condition")
    if action not in P.CONTROL_DECISIONS:
        raise ValueError("controller action is invalid")
    if not isinstance(rationale, str):
        raise ValueError("controller rationale must be a string")
    if not isinstance(protected, list) or any(
        not isinstance(item, str) for item in protected
    ):
        raise ValueError("protected_invariants must be a list of strings")
    if not isinstance(acceptance, str):
        raise ValueError("verification_acceptance_condition must be a string")

    if action == "stop":
        if instruction is not None or protected or acceptance:
            raise ValueError("stop requires null/empty auxiliary fields")
        return {
            "sample_id": (packet or {}).get("sample_id", ""),
            "control_decision": {"action": "stop", "rationale": rationale},
        }

    if not isinstance(instruction, dict):
        raise ValueError("worker_instruction must be an object")
    if set(instruction) != _WORKER_INSTRUCTION_FIELDS:
        raise ValueError("worker_instruction has unexpected fields")
    goal = instruction.get("goal")
    context = instruction.get("context")
    required_outcomes = instruction.get("required_outcomes")
    prohibited_actions = instruction.get("prohibited_actions")
    completion = instruction.get("completion_condition")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("worker_instruction.goal must be non-empty")
    if not isinstance(context, str):
        raise ValueError("worker_instruction.context must be a string")
    if not isinstance(required_outcomes, list) or any(
        not isinstance(item, str) for item in required_outcomes
    ):
        raise ValueError("required_outcomes must be a list of strings")
    if not isinstance(prohibited_actions, list) or any(
        not isinstance(item, str) for item in prohibited_actions
    ):
        raise ValueError("prohibited_actions must be a list of strings")
    if not isinstance(completion, str) or not completion.strip():
        raise ValueError("worker_instruction.completion_condition must be non-empty")
    return {
        "sample_id": (packet or {}).get("sample_id", ""),
        "control_decision": {"action": action, "rationale": rationale},
        "control_instruction": {
            "goal": goal,
            "required_actions": required_outcomes,
            "disallowed_actions": prohibited_actions,
            "notes_for_worker": [context] if context.strip() else [],
            "completion_condition": completion,
        },
        "protected_invariants": protected,
        "verification_plan": {"acceptance_condition": acceptance.strip() or completion},
    }


_EVIDENCE_SECTION_START = (
    "## Original coding-agent turns selected by the reporting agent"
)
_EVIDENCE_SECTION_END = "## Remaining coding budget"


def _canonical_messages_text(messages: list[Message]) -> str:
    return json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _messages_characters(messages: list[Message]) -> int:
    return len(_canonical_messages_text(messages))


def _compact_historical_evidence(
    content: str,
) -> tuple[str, dict[str, Any] | None]:
    """Remove only an older packet's expanded evidence bodies.

    Reporter prose, evidence labels, and the surrounding Controller
    conversation stay visible. The full source remains in the packet and run
    artifacts; this function changes only the next model request.
    """

    start = content.find(_EVIDENCE_SECTION_START)
    if start < 0:
        return content, None
    end = content.find(_EVIDENCE_SECTION_END, start)
    if end < 0:
        return content, None
    section = content[start:end].rstrip()
    labels = re.findall(r"^### Coding-agent turns (.+)$", section, flags=re.M)
    if not labels:
        return content, None
    refs = ", ".join(labels)
    replacement = "\n".join(
        [
            _EVIDENCE_SECTION_START,
            "",
            "<historical_evidence_omitted",
            f'  refs="{refs}"',
            f'  original_characters="{len(section)}"',
            ">",
            "Expanded evidence from this older Controller round was omitted",
            "deterministically to stay within the shared input budget. The",
            "Reporter report, Controller decision, references, and complete run",
            "artifacts remain available.",
            "</historical_evidence_omitted>",
            "",
        ]
    )
    compacted = content[:start] + replacement + content[end:]
    return compacted, {
        "refs": labels,
        "original_characters": len(section),
        "visible_characters": len(replacement.rstrip()),
    }


def prepare_controller_messages(
    packet: dict,
    previous_messages: list[Message] | None = None,
) -> tuple[list[Message], dict[str, Any]]:
    """Build Controller messages and apply overflow-only deterministic views."""

    errors = V.validate_packet(packet)
    if errors:
        raise ValueError("invalid packet: " + json.dumps(errors, ensure_ascii=False))
    first_turn = not previous_messages
    if previous_messages:
        messages = copy.deepcopy(previous_messages)
        if messages[0] != {"role": "system", "content": R.CONTROLLER_SYSTEM_PROMPT}:
            raise ValueError("controller conversation system prompt mismatch")
        if messages[-1].get("role") != "assistant":
            raise ValueError("controller conversation must end with its prior response")
        messages.append(
            {
                "role": "user",
                "content": R.render_controller_packet(packet, first_turn=False),
            }
        )
    else:
        messages = [
            {"role": "system", "content": R.CONTROLLER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": R.render_controller_packet(packet, first_turn=True),
            },
        ]

    original_messages = copy.deepcopy(messages)
    original_characters = _messages_characters(messages)
    audit: dict[str, Any] = {
        "policy": V.capacity_policy_identity(),
        "original_input_characters": original_characters,
        "visible_input_characters": original_characters,
        "compaction_applied": False,
        "current_evidence_segments": [],
        "historical_evidence_messages": [],
        "effective_evidence_segment_max_characters": None,
        "preflight_exhausted": False,
    }
    if original_characters <= V.CONTROLLER_INPUT_MAX_CHARACTERS:
        return messages, audit

    # Only overflowed requests take the compacted rendering path. Under-limit
    # model inputs remain byte-for-byte identical to the pre-policy renderer.
    current_audits: list[dict[str, Any]] = []
    messages[-1] = {
        "role": "user",
        "content": R.render_controller_packet(
            packet,
            first_turn=first_turn,
            evidence_segment_max_characters=(
                V.CONTROLLER_EVIDENCE_SEGMENT_MAX_CHARACTERS
            ),
            evidence_compaction_audit=current_audits,
        ),
    }
    audit["current_evidence_segments"] = current_audits
    audit["effective_evidence_segment_max_characters"] = (
        V.CONTROLLER_EVIDENCE_SEGMENT_MAX_CHARACTERS
    )

    # Preserve recent evidence when possible: compact older user packets in
    # chronological order and stop as soon as the total request fits.
    for index, message in enumerate(messages[:-1]):
        if _messages_characters(messages) <= V.CONTROLLER_INPUT_MAX_CHARACTERS:
            break
        if message.get("role") != "user":
            continue
        compacted, historical_audit = _compact_historical_evidence(
            str(message.get("content") or "")
        )
        if historical_audit is None:
            continue
        message["content"] = compacted
        historical_audit["message_index"] = index
        audit["historical_evidence_messages"].append(historical_audit)

    # A current packet can cite many separate ranges. If 32,768 characters per
    # range is still too large after old evidence is omitted, find the largest
    # deterministic per-range limit that fits, down to a 2,048-character floor.
    if _messages_characters(messages) > V.CONTROLLER_INPUT_MAX_CHARACTERS:
        low = 2_048
        high = V.CONTROLLER_EVIDENCE_SEGMENT_MAX_CHARACTERS - 1
        best: tuple[int, str, list[dict[str, Any]]] | None = None
        while low <= high:
            candidate_limit = (low + high) // 2
            candidate_audits: list[dict[str, Any]] = []
            candidate_content = R.render_controller_packet(
                packet,
                first_turn=first_turn,
                evidence_segment_max_characters=candidate_limit,
                evidence_compaction_audit=candidate_audits,
            )
            candidate_messages = [
                *copy.deepcopy(messages[:-1]),
                {
                    "role": "user",
                    "content": candidate_content,
                },
            ]
            if (
                _messages_characters(candidate_messages)
                <= V.CONTROLLER_INPUT_MAX_CHARACTERS
            ):
                best = (candidate_limit, candidate_content, candidate_audits)
                low = candidate_limit + 1
            else:
                high = candidate_limit - 1
        if best is not None:
            chosen_limit, chosen_content, chosen_audits = best
            messages[-1] = {"role": "user", "content": chosen_content}
            audit["current_evidence_segments"] = chosen_audits
            audit["effective_evidence_segment_max_characters"] = chosen_limit

    visible_characters = _messages_characters(messages)
    audit.update(
        {
            "visible_input_characters": visible_characters,
            "compaction_applied": messages != original_messages,
            "preflight_exhausted": (
                visible_characters > V.CONTROLLER_INPUT_MAX_CHARACTERS
            ),
        }
    )
    return messages, audit


def controller_messages(
    packet: dict,
    previous_messages: list[Message] | None = None,
) -> list[Message]:
    messages, audit = prepare_controller_messages(packet, previous_messages)
    if audit["preflight_exhausted"]:
        raise RuntimeError(
            "controller_context_preflight_exhausted:"
            f"visible_characters={audit['visible_input_characters']}:"
            f"limit_characters={V.CONTROLLER_INPUT_MAX_CHARACTERS}"
        )
    return messages


def run_controller(
    packet: dict,
    controller: ChatClient,
    *,
    max_tokens: int = CONTROLLER_MAX_OUTPUT_TOKENS,
    previous_messages: list[Message] | None = None,
) -> ControllerResult:
    messages, context_compaction_audit = prepare_controller_messages(
        packet,
        previous_messages,
    )
    model_call_audits: list[dict] = []
    contract: dict = {}
    raw = ""
    errors: list[str] = []
    failure_kind = ""
    failure_diagnostics: dict[str, Any] = {}
    request_messages = copy.deepcopy(messages)
    if context_compaction_audit["preflight_exhausted"]:
        errors = ["controller_context_preflight_exhausted"]
        failure_kind = "controller_context_preflight_exhausted"
        return ControllerResult(
            contract={},
            messages=messages,
            raw_response="",
            validation_errors=errors,
            failure_kind=failure_kind,
            context_compaction_audit=context_compaction_audit,
        )
    try:
        before_audits = len(getattr(controller, "call_audits", []))
        raw = controller(request_messages, max_tokens=max_tokens)
        call_audits = getattr(controller, "call_audits", [])
        if isinstance(call_audits, list):
            model_call_audits.extend(copy.deepcopy(call_audits[before_audits:]))
    except Exception as exc:
        call_audits = getattr(controller, "call_audits", [])
        if isinstance(call_audits, list):
            model_call_audits.extend(copy.deepcopy(call_audits[before_audits:]))
        raw = ""
        contract = {}
        failure_kind = str(getattr(exc, "failure_kind", "infrastructure_transport"))
        error_code = str(getattr(exc, "error_code", type(exc).__name__))
        failure_diagnostics = {
            "error_type": type(exc).__name__,
            "error_code": error_code,
            "failure_kind": failure_kind,
        }
        safe_message = getattr(exc, "safe_message", "")
        if isinstance(safe_message, str) and safe_message:
            failure_diagnostics["redacted_error"] = safe_message[:500]
        error_prefix = (
            "controller_submission_error"
            if failure_kind == "invalid_contract"
            else "controller_transport_error"
        )
        errors = [f"{error_prefix}:{error_code}"]
    else:
        try:
            visible_contract = extract_json_object(raw)
            contract = normalize_controller_contract(visible_contract, packet)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            contract = {}
            errors = [f"controller_response_invalid_json:{type(exc).__name__}"]
            failure_kind = "invalid_contract"
        else:
            errors = V.validate_contract(contract, packet)
            if errors:
                failure_kind = "invalid_contract"
    return ControllerResult(
        contract=contract,
        messages=messages,
        raw_response=raw,
        validation_errors=errors,
        failure_kind=failure_kind,
        failure_diagnostics=failure_diagnostics,
        model_call_audits=model_call_audits,
        context_compaction_audit=context_compaction_audit,
    )
