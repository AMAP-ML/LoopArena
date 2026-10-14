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

# Controller response budget.
CONTROLLER_MAX_OUTPUT_TOKENS = 20_480
CONTROLLER_OUTPUT_LIMIT_EXHAUSTED = "controller_output_limit_exhausted"
_OUTPUT_LIMIT_REASONS = {"length", "max_tokens", "max_output_tokens"}


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




def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from model output."""

    contract, _ = extract_json_object_with_repairs(text)
    return contract


def normalize_controller_contract(contract: dict, packet: dict | None = None) -> dict:
    """Map model-facing supervisor JSON onto the internal contract schema."""
    if not isinstance(contract, dict):
        raise ValueError("controller response must be a JSON object")

    contract, _ = repair_model_visible_controller_contract(contract)

    top_level_action = contract.get("action")
    nested_decision = contract.get("control_decision")
    nested_action = (
        nested_decision.get("action")
        if isinstance(nested_decision, dict)
        else None
    )
    submitted_action = top_level_action if "action" in contract else nested_action
    # Stop is action-only at runtime.  The prompt asks for a rationale so runs
    # remain interpretable, but missing or malformed auxiliary fields must
    # never restart work or turn an explicit stop into an invalid contract.
    if isinstance(submitted_action, str) and submitted_action.strip() == "stop":
        rationale = contract.get("rationale")
        if not isinstance(rationale, str) and isinstance(nested_decision, dict):
            rationale = nested_decision.get("rationale")
        return {

            "sample_id": (packet or {}).get("sample_id", ""),
            "control_decision": {
                "action": "stop",
                "rationale": rationale if isinstance(rationale, str) else "",
            },
        }

    if "action" in contract and "control_decision" not in contract:
        action = contract.get("action")
        rationale = contract.get("rationale", "")
        instruction = contract.get("worker_instruction")
        protected = contract.get("protected_invariants", [])
        acceptance = contract.get("verification_acceptance_condition", "")
        if action not in P.CONTROL_DECISIONS:
            raise ValueError("simplified controller action is invalid")
        if not isinstance(rationale, str):
            rationale = str(rationale or "")
        if not isinstance(instruction, dict):
            raise ValueError("simplified worker_instruction must be an object")
        goal = instruction.get("goal")
        completion = instruction.get("completion_condition")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("simplified worker_instruction.goal must be non-empty")
        if not isinstance(completion, str) or not completion.strip():
            raise ValueError(
                "simplified worker_instruction.completion_condition must be non-empty"
            )
        required_outcomes = instruction.get("required_outcomes")
        prohibited_actions = instruction.get("prohibited_actions")
        if not isinstance(required_outcomes, list):
            required_outcomes = []
        if not isinstance(prohibited_actions, list):
            prohibited_actions = []
        if not isinstance(protected, list):
            protected = []
        if not isinstance(acceptance, str):
            acceptance = ""
        context = str(instruction.get("context") or "").strip()
        return {

            "sample_id": (packet or {}).get("sample_id", ""),
            "control_decision": {
                "action": action,
                "rationale": rationale,
            },
            "state_update": {
                "open_requirements": [],
                "verified_requirements": [],
                "stale_or_unsupported_claims": [],
                "failed_or_incomplete_attempts": [],
                "blockers": [],
            },
            "suspected_failure_mode": "",
            "control_instruction": {
                "goal": goal,
                "target_scope_refs": [],
                "required_actions": [str(item) for item in required_outcomes],
                "disallowed_actions": [str(item) for item in prohibited_actions],
                "notes_for_worker": [context] if context else [],
                "completion_condition": completion,
            },
            "protected_invariants": [str(item) for item in protected],
            "verification_plan": {
                "acceptance_condition": acceptance.strip() or completion
            },
            "budget_hint": "",
            "evidence_refs": {"claim_to_evidence": []},
        }
    out = dict(contract)
    if packet is not None and not out.get("sample_id"):
        out["sample_id"] = packet.get("sample_id", "")
    # This field is prose shown to the worker.  OpenAI-compatible models
    # commonly emit one string even though the canonical contract stores a
    # list.  Preserve the prose instead of failing an otherwise valid control
    # decision on this non-semantic container difference.
    instruction = out.get("control_instruction")
    if isinstance(instruction, dict) and isinstance(
        instruction.get("notes_for_worker"), str
    ):
        normalized_instruction = dict(instruction)
        note = instruction["notes_for_worker"].strip()
        normalized_instruction["notes_for_worker"] = [note] if note else []
        out["control_instruction"] = normalized_instruction
    # The validator already permits an empty evidence mapping, so omission and
    # an explicitly empty mapping carry the same meaning.  Canonicalize the
    # omission instead of terminating the episode on JSON boilerplate alone.
    evidence_refs = out.get("evidence_refs")
    if isinstance(evidence_refs, list):
        out["evidence_refs"] = {"claim_to_evidence": evidence_refs}
    elif "evidence_refs" not in out:
        out["evidence_refs"] = {"claim_to_evidence": []}
    return out


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


def run_controller(packet: dict, controller: ChatClient, *, max_tokens: int=CONTROLLER_MAX_OUTPUT_TOKENS, previous_messages: list[Message] | None=None) -> ControllerResult:
    (messages, context_compaction_audit) = prepare_controller_messages(packet, previous_messages)
    model_call_audits: list[dict] = []
    contract: dict = {}
    raw = ''
    errors: list[str] = []
    failure_kind = ''
    failure_diagnostics: dict[str, Any] = {}
    request_messages = copy.deepcopy(messages)
    if context_compaction_audit['preflight_exhausted']:
        errors = ['controller_context_preflight_exhausted']
        failure_kind = 'controller_context_preflight_exhausted'
        return ControllerResult(contract={}, messages=messages, raw_response='', validation_errors=errors, failure_kind=failure_kind, context_compaction_audit=context_compaction_audit)
    try:
        before_audits = len(getattr(controller, 'call_audits', []))
        raw = controller(request_messages, max_tokens=max_tokens)
        call_audits = getattr(controller, 'call_audits', [])
        if isinstance(call_audits, list):
            model_call_audits.extend(copy.deepcopy(call_audits[before_audits:]))
    except Exception as exc:
        call_audits = getattr(controller, 'call_audits', [])
        if isinstance(call_audits, list):
            model_call_audits.extend(copy.deepcopy(call_audits[before_audits:]))
        raw = ''
        contract = {}
        failure_kind = str(getattr(exc, 'failure_kind', 'infrastructure_transport'))
        error_code = str(getattr(exc, 'error_code', type(exc).__name__))
        failure_diagnostics = {'error_type': type(exc).__name__, 'error_code': error_code, 'failure_kind': failure_kind}
        safe_message = getattr(exc, 'safe_message', '')
        if isinstance(safe_message, str) and safe_message:
            failure_diagnostics['redacted_error'] = safe_message[:500]
        error_prefix = 'controller_submission_error' if failure_kind == 'invalid_contract' else 'controller_transport_error'
        errors = [f'{error_prefix}:{error_code}']
    else:
        if controller_output_limit_exhausted(model_call_audits):
            errors = [CONTROLLER_OUTPUT_LIMIT_EXHAUSTED]
            failure_kind = CONTROLLER_OUTPUT_LIMIT_EXHAUSTED
        else:
            try:
                visible_contract = extract_json_object(raw)
                contract = normalize_controller_contract(visible_contract, packet)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                contract = {}
                errors = [f'controller_response_invalid_json:{type(exc).__name__}']
                failure_kind = 'invalid_contract'
            else:
                errors = V.validate_contract(contract, packet)
                if errors:
                    failure_kind = 'invalid_contract'
    return ControllerResult(contract=contract, messages=messages, raw_response=raw, validation_errors=errors, failure_kind=failure_kind, failure_diagnostics=failure_diagnostics, model_call_audits=model_call_audits, context_compaction_audit=context_compaction_audit)


def controller_output_limit_exhausted(model_call_audits: list[dict]) -> bool:
    """Return whether the completed Controller response hit its output limit."""

    for call in reversed(model_call_audits):
        response = call.get("response_audit") if isinstance(call, dict) else None
        if not isinstance(response, dict):
            continue
        finish_reason = str(response.get("finish_reason") or "").lower()
        status = str(response.get("status") or "").lower()
        details = response.get("incomplete_details")
        incomplete_reason = (
            str(details.get("reason") or "").lower()
            if isinstance(details, dict)
            else ""
        )
        return finish_reason in _OUTPUT_LIMIT_REASONS or (
            status == "incomplete" and incomplete_reason in _OUTPUT_LIMIT_REASONS
        )
    return False


def _remove_json_trailing_commas(text: str) -> tuple[str, bool]:
    """Remove commas immediately before ``}``/``]`` outside JSON strings."""

    out: list[str] = []
    in_string = False
    escape = False
    changed = False
    index = 0
    while index < len(text):
        ch = text[index]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            index += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            index += 1
            continue
        if ch == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                changed = True
                index += 1
                continue
        out.append(ch)
        index += 1
    return "".join(out), changed


def _extract_json_object_without_repairs(cleaned: str) -> dict:
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("model response contains no JSON object")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        ch = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : index + 1])
    raise ValueError("model JSON object is not closed")


def extract_json_object_with_repairs(text: str) -> tuple[dict, list[str]]:
    """Parse JSON with deterministic handling of commas and object boundaries."""

    cleaned = re.sub(r"```(?:json)?", "", text or "")
    original_error: ValueError | json.JSONDecodeError
    try:
        return _extract_json_object_without_repairs(cleaned), []
    except (ValueError, json.JSONDecodeError) as exc:
        original_error = exc

    candidates: list[tuple[str, list[str]]] = []
    without_trailing_commas, removed = _remove_json_trailing_commas(cleaned)
    if removed:
        candidates.append(
            (without_trailing_commas, ["removed_json_trailing_comma"])
        )

    worker_marker = cleaned.find('"worker_instruction"')
    protected_match = re.search(r',\s*"protected_invariants"\s*:', cleaned)
    if (
        worker_marker >= 0
        and protected_match is not None
        and protected_match.start() > worker_marker
    ):
        inserted = (
            cleaned[: protected_match.start()]
            + "}"
            + cleaned[protected_match.start() :]
        )
        candidates.append(
            (
                inserted,
                ["closed_worker_instruction_before_protected_invariants"],
            )
        )
        inserted_without_trailing, inserted_removed = _remove_json_trailing_commas(
            inserted
        )
        if inserted_removed:
            candidates.append(
                (
                    inserted_without_trailing,
                    [
                        "closed_worker_instruction_before_protected_invariants",
                        "removed_json_trailing_comma",
                    ],
                )
            )

    for candidate, repairs in candidates:
        try:
            parsed = _extract_json_object_without_repairs(candidate)
        except (ValueError, json.JSONDecodeError):
            continue
        if (
            parsed.get("action") in {"advance", "verify"}
            and isinstance(parsed.get("worker_instruction"), dict)
            and "protected_invariants" in parsed
        ) or repairs == ["removed_json_trailing_comma"]:
            return parsed, repairs
    raise original_error


def repair_model_visible_controller_contract(contract: dict) -> tuple[dict, list[str]]:
    """Promote known sibling fields misplaced inside ``worker_instruction``."""

    if not isinstance(contract, dict):
        raise ValueError("controller response must be a JSON object")
    repaired = copy.deepcopy(contract)
    repairs: list[str] = []
    if repaired.get("action") not in {"advance", "verify"}:
        return repaired, repairs
    instruction = repaired.get("worker_instruction")
    if not isinstance(instruction, dict):
        return repaired, repairs
    for field_name in (
        "protected_invariants",
        "verification_acceptance_condition",
    ):
        if field_name not in repaired and field_name in instruction:
            repaired[field_name] = copy.deepcopy(instruction[field_name])
            repairs.append(f"promoted_worker_instruction.{field_name}")
    return repaired, repairs
