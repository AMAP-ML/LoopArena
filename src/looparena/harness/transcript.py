"""Utilities for the public Worker transcript."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

_EVENT_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_WORKER_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*_event:(\d+):(\d+):")


def derive_public_tool_events(
    messages: list[dict[str, Any]],
    *,
    event_namespace: str = "main_event",
) -> list[dict[str, Any]]:
    """Pair public tool calls with results and assign deterministic event refs."""

    if not _EVENT_NAMESPACE_RE.fullmatch(event_namespace):
        raise ValueError("event_namespace must be a lowercase identifier")

    tool_results: dict[str, str] = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(
                f"public transcript message {message_index} must be an object"
            )
        if message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        if not call_id:
            raise ValueError("public transcript tool result is missing tool_call_id")
        if call_id in tool_results:
            raise ValueError("public transcript contains duplicate tool result ids")
        tool_results[call_id] = str(message.get("content") or "")

    events: list[dict[str, Any]] = []
    used_result_ids: set[str] = set()
    seen_call_ids: set[str] = set()
    assistant_turn = 0
    for message in messages:
        if message.get("role") != "assistant":
            continue
        assistant_turn += 1
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError("public transcript tool_calls must be an array")
        for tool_index, call in enumerate(calls):
            if not isinstance(call, dict):
                raise ValueError("public transcript contains an invalid tool call")
            call_id = str(call.get("id") or "")
            if not call_id:
                raise ValueError("public transcript tool call is missing id")
            if call_id in seen_call_ids:
                raise ValueError("public transcript contains duplicate tool call ids")
            seen_call_ids.add(call_id)
            if call_id not in tool_results:
                raise ValueError(
                    "public transcript tool call is missing its public result"
                )

            function = call.get("function") or {}
            if not isinstance(function, dict):
                raise ValueError("public transcript contains an invalid tool function")
            name = str(function.get("name") or "")
            raw_arguments = function.get("arguments")
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "public transcript contains malformed tool arguments"
                    ) from exc
            else:
                arguments = raw_arguments
            if not name or not isinstance(arguments, dict):
                raise ValueError("public transcript contains an invalid tool call")

            safe_name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
            events.append(
                {
                    "event_id": (
                        f"{event_namespace}:{assistant_turn}:{tool_index}:"
                        f"{safe_name or 'tool'}"
                    ),
                    "name": name,
                    "arguments": arguments,
                    "result": tool_results[call_id],
                    "tool_call_id": call_id,
                }
            )
            used_result_ids.add(call_id)

    if set(tool_results) - used_result_ids:
        raise ValueError("public transcript contains orphan tool results")
    return events


def worker_evidence_ref(event: object) -> str:
    """Return the evidence reference for one complete worker assistant turn."""

    if not isinstance(event, dict):
        return ""
    explicit = str(event.get("evidence_ref") or "")
    if re.fullmatch(r"E[1-9]\d*", explicit):
        return explicit
    match = _WORKER_EVENT_RE.match(str(event.get("event_id") or ""))
    if match is None:
        return ""
    turn = int(match.group(1))
    return f"E{turn}"


def derive_worker_evidence_turns(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive one lossless evidence record per observable worker assistant turn.

    Assistant text is preserved as a worker statement.  Each tool call is paired
    with the exact result visible in the public conversation.  The function does
    not summarize, truncate, or assign evidentiary strength to either source.
    """

    tool_results: dict[str, str] = {}
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(
                f"public transcript message {message_index} must be an object"
            )
        if message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        if not call_id:
            raise ValueError("public transcript tool result is missing tool_call_id")
        if call_id in tool_results:
            raise ValueError("public transcript contains duplicate tool result ids")
        tool_results[call_id] = str(message.get("content") or "")

    turns: list[dict[str, Any]] = []
    used_result_ids: set[str] = set()
    seen_call_ids: set[str] = set()
    assistant_turn = 0
    for message in messages:
        if message.get("role") != "assistant":
            continue
        assistant_turn += 1
        content = message.get("content")
        if isinstance(content, str):
            assistant_text = content
        elif content is None:
            assistant_text = ""
        else:
            assistant_text = json.dumps(content, ensure_ascii=False, indent=2)

        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError("public transcript tool_calls must be an array")
        interactions: list[dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, dict):
                raise ValueError("public transcript contains an invalid tool call")
            call_id = str(call.get("id") or "")
            if not call_id:
                raise ValueError("public transcript tool call is missing id")
            if call_id in seen_call_ids:
                raise ValueError("public transcript contains duplicate tool call ids")
            seen_call_ids.add(call_id)
            if call_id not in tool_results:
                raise ValueError(
                    "public transcript tool call is missing its public result"
                )
            function = call.get("function") or {}
            if not isinstance(function, dict):
                raise ValueError("public transcript contains an invalid tool function")
            name = str(function.get("name") or "")
            interactions.append(
                {
                    # A malformed model response can omit the tool name.  The
                    # worker loop records that rejected call and its public
                    # error result, so keep it visible to the Reporter instead
                    # of making the later evidence pass impossible.
                    "tool_name": name or "<missing tool name>",
                    "arguments": copy.deepcopy(function.get("arguments")),
                    "recorded_result": tool_results[call_id],
                    "tool_call_id": call_id,
                }
            )
            used_result_ids.add(call_id)
        turns.append(
            {
                "evidence_ref": f"E{assistant_turn}",
                "assistant_turn": assistant_turn,
                "assistant_text": assistant_text,
                "tool_interactions": interactions,
            }
        )

    if set(tool_results) - used_result_ids:
        raise ValueError("public transcript contains orphan tool results")
    return turns
