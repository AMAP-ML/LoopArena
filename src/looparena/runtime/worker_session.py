#!/usr/bin/env python3
"""Current continuous main-worker and forked-reporter session runtime."""

from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from .worker_tools import (
    MAIN_REPOSITORY_TOOLS,
    MAX_CONSECUTIVE_EMPTY_ASSISTANT_RESPONSES,
    REPO_TOOL_NAMES,
    REPORTER_TOOLS,
    WORKER_SYSTEM,
    _assistant_history_message,
    _chat_with_deadline,
    _deadline_exceeded,
    _decode_tool_arguments,
    _exec_tool_event,
    _main_event_id,
    _nonempty_tool_result,
    _redact_error_text,
    _response_audit,
)

REPORTER_MAX_OUTPUT_TOKENS = 8192
WORKER_INPUT_MAX_UTF8_BYTES = 3_000_000
EXPIRED_TOOL_OUTPUT = "Earlier conversation history has expired."
_TRUNCATED_FINISH_REASONS = {
    "length",
    "max_tokens",
    "max_output_tokens",
}


def _worker_input_utf8_bytes(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> int:
    return len(
        json.dumps(
            {"messages": messages, "tools": tools},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _worker_visible_messages(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    max_utf8_bytes: int = WORKER_INPUT_MAX_UTF8_BYTES,
) -> list[dict[str, Any]]:
    """Mask the oldest tool outputs only when the provider input is too large."""

    input_bytes = _worker_input_utf8_bytes(messages, tools)
    if input_bytes <= max_utf8_bytes:
        return messages

    visible = copy.deepcopy(messages)
    replacement_bytes = len(
        json.dumps(EXPIRED_TOOL_OUTPUT, ensure_ascii=False).encode("utf-8")
    )
    for message in visible:
        content = message.get("content")
        if message.get("role") != "tool" or not isinstance(content, str):
            continue
        content_bytes = len(json.dumps(content, ensure_ascii=False).encode("utf-8"))
        if content_bytes <= replacement_bytes:
            continue
        message["content"] = EXPIRED_TOOL_OUTPUT
        input_bytes -= content_bytes - replacement_bytes
        if input_bytes <= max_utf8_bytes:
            return visible

    raise RuntimeError(
        "context_capacity_exhausted:"
        "worker_history_compaction_insufficient:"
        f"input_utf8_bytes={input_bytes}:"
        f"max_utf8_bytes={max_utf8_bytes}"
    )


def _response_was_truncated(message: dict[str, Any]) -> bool:
    audit = _response_audit(message)
    return (
        str(audit.get("finish_reason") or "").strip().lower()
        in _TRUNCATED_FINISH_REASONS
    )


def _append_unexecuted_truncated_tool_results(
    messages: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> None:
    """Close incomplete tool-call messages without executing partial output."""

    for call in calls:
        tool_message = {
            "role": "tool",
            "tool_call_id": call.get("id", ""),
            "content": (
                "error: the model response reached its output limit; this "
                "possibly incomplete tool call was not executed"
            ),
        }
        messages.append(tool_message)


def _reporter_max_output_tokens(
    worker: Any,
    messages: list[dict[str, Any]],
) -> int:
    """Preserve the preregistered cap while fitting the fail-closed context."""

    capacity = getattr(worker, "context_capacity_utf8_bytes", None)
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        return REPORTER_MAX_OUTPUT_TOKENS
    input_bytes = len(
        json.dumps(
            {"messages": messages, "tools": REPORTER_TOOLS},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    available_output_tokens = max(0, capacity - input_bytes) // 4
    return max(
        1,
        min(REPORTER_MAX_OUTPUT_TOKENS, available_output_tokens),
    )


def _round_report_correction_prompt(errors: list[str]) -> str:
    """Return deterministic, actionable feedback for one rejected report."""

    return "\n".join(
        [
            "# Correct the round_report submission",
            "",
            "The previous round_report was not accepted.",
            "",
            "The tool result immediately above contains the exact validation errors:",
            json.dumps(errors, ensure_ascii=False),
            "",
            "Revise the same factual report. Do not repeat completed repository",
            "inspection only because the report format was rejected.",
            "",
            "- Include exactly these four non-empty Markdown fields:",
            "  task_context_and_constraints, work_history_and_current_state,",
            "  verification_and_evidence, and open_issues_and_uncertainty.",
            "- If E labels were shown, select at least one material turn using",
            "  complete brackets: [E12], [E12, E13], or [E12-E15].",
            "- Use only E labels shown in the quoted history. Bare labels such as",
            "  E12 are ordinary prose and do not select evidence.",
            "- In a range, put the earlier E number first and include only labels",
            "  that appear in the quoted history.",
            "",
            "Now call round_report again as the sole tool call.",
        ]
    )


class WorkerConversation:
    """One complete observable fixed-worker conversation for one harness arm."""

    def __init__(
        self,
        worker: Any,
        sandbox: Any,
        workdir: Path,
        *,
        system_prompt: str | None = None,
        mount_point: str = "/work",
    ) -> None:
        self.worker = worker
        self.sandbox = sandbox
        self.workdir = Path(workdir)
        self.system_prompt = system_prompt or WORKER_SYSTEM
        self.mount_point = mount_point
        self.messages: list[dict[str, Any]] = []
        self.total_repo_actions = 0
        self.total_model_calls = 0

    def initialize(self, common_start_user_turn: str) -> None:
        if self.messages:
            raise RuntimeError("worker conversation is already initialized")
        if (
            not isinstance(common_start_user_turn, str)
            or not common_start_user_turn.strip()
        ):
            raise ValueError("common conversation start must be non-empty")
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": common_start_user_turn},
        ]

    def append_user_turn(self, content: str) -> None:
        """Append one deterministic start or control user turn."""

        if not isinstance(content, str) or not content.strip():
            raise ValueError("worker user turn must be non-empty")
        self.messages.append({"role": "user", "content": content})

    def run_main_until_boundary(
        self,
        *,
        arm: str,
        control_decision: str = "",
        turns_remaining: int,
        seed: int = 0,
        wall_time_limit_sec: int | float | None = None,
        progress_sink: Callable[[dict[str, Any]], None] | None = None,
        initial_empty_responses: int = 0,
        initial_protocol_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run the current natural-inner-loop main-worker interface."""

        message_start_index = len(self.messages)
        result = run_main_worker_until_boundary(
            self.worker,
            self.sandbox,
            self.workdir,
            self.messages,
            arm=arm,
            control_decision=control_decision,
            turns_remaining=turns_remaining,
            turns_already_used=self.total_model_calls,
            seed=seed,
            mount_point=self.mount_point,
            wall_time_limit_sec=wall_time_limit_sec,
            progress_sink=progress_sink,
            initial_empty_responses=initial_empty_responses,
            initial_protocol_errors=initial_protocol_errors,
        )
        self.messages = copy.deepcopy(result.pop("messages"))
        result["message_range"] = [message_start_index, len(self.messages)]
        self.total_model_calls += int(result.get("main_worker_turns") or 0)
        self.total_repo_actions += int(result.get("repo_tool_calls") or 0)
        return result

    def run_forked_reporter(
        self,
        *,
        reporter_sandbox: Any,
        reporter_prompt: str,
        reporter_system_prompt: str | None = None,
        max_reporter_turns: int = 50,
        seed: int = 0,
        wall_time_limit_sec: int | float | None = None,
        report_validator: Callable[[dict], list[str]] | None = None,
        report_event_validator: Callable[[dict, list[dict[str, Any]]], list[str]]
        | None = None,
        initial_state: dict[str, Any] | None = None,
        progress_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run a private reporter fork without mutating this conversation."""

        before = copy.deepcopy(self.messages)
        result = run_forked_reporter(
            self.worker,
            reporter_sandbox,
            self.workdir,
            before,
            reporter_prompt,
            reporter_system_prompt=reporter_system_prompt,
            max_reporter_turns=max_reporter_turns,
            seed=seed,
            mount_point=self.mount_point,
            wall_time_limit_sec=wall_time_limit_sec,
            report_validator=report_validator,
            report_event_validator=report_event_validator,
            # Reporter messages are retained once in reporter_runs. Recovery
            # uses the pending Reporter state instead of copying the same
            # conversation into the main trajectory stream.
            initial_state=initial_state,
            progress_sink=progress_sink,
        )
        if self.messages != before:
            raise RuntimeError("reporter mutated the main worker conversation")
        return result


def run_main_worker_until_boundary(
    worker: Any,
    sandbox: Any,
    workdir: Path,
    messages: list[dict[str, Any]],
    *,
    arm: str,
    control_decision: str = "",
    turns_remaining: int,
    turns_already_used: int = 0,
    seed: int = 0,
    mount_point: str = "/work",
    wall_time_limit_sec: int | float | None = None,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
    initial_empty_responses: int = 0,
    initial_protocol_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Run the main worker until the arm-specific handback or budget boundary.

    One ReAct turn is one main-worker model response. Repository tool calls are
    counted separately and never substitute for the 600-turn budget. A normal
    final answer ends the no-control episode. In controlled, the same natural
    final closes only the current inner loop so the harness can run the forked
    reporter and obtain the next controller decision.
    """

    if arm not in {"no-control", "controlled"}:
        raise ValueError("arm must be no-control or controlled")
    if (
        isinstance(turns_remaining, bool)
        or not isinstance(turns_remaining, int)
        or turns_remaining < 0
    ):
        raise ValueError("turns_remaining must be a non-negative integer")
    if (
        isinstance(initial_empty_responses, bool)
        or not isinstance(initial_empty_responses, int)
        or not 0 <= initial_empty_responses < MAX_CONSECUTIVE_EMPTY_ASSISTANT_RESPONSES
    ):
        raise ValueError("initial_empty_responses is outside the resumable range")
    if initial_protocol_errors is not None and (
        not isinstance(initial_protocol_errors, list)
        or not all(isinstance(item, str) for item in initial_protocol_errors)
    ):
        raise ValueError("initial_protocol_errors must be a list of strings")
    history = copy.deepcopy(messages)
    tools = MAIN_REPOSITORY_TOOLS
    deadline = (
        time.monotonic() + float(wall_time_limit_sec)
        if wall_time_limit_sec is not None and float(wall_time_limit_sec) > 0
        else None
    )
    tool_events: list[dict[str, Any]] = []
    response_audit: list[dict[str, Any]] = []
    main_turns = 0
    repo_tool_calls = 0
    empty_responses = int(initial_empty_responses)
    status = "budget_exhausted" if turns_remaining == 0 else "running"
    termination_reason = "main_worker_budget_exhausted" if turns_remaining == 0 else ""
    final_answer = ""
    provider_error = ""
    protocol_errors: list[str] = list(initial_protocol_errors or [])
    progress_started = time.monotonic()

    def save_safe_progress() -> None:
        if progress_sink is not None:
            progress_sink(
                {
                    "messages": copy.deepcopy(history),
                    "main_worker_turns": main_turns,
                    "repo_tool_calls": repo_tool_calls,
                    "tool_events": copy.deepcopy(tool_events),
                    "model_response_audit": copy.deepcopy(response_audit),
                    "consecutive_empty_responses": empty_responses,
                    "protocol_errors": copy.deepcopy(protocol_errors),
                    "wall_time_sec": round(time.monotonic() - progress_started, 6),
                }
            )

    while main_turns < turns_remaining:
        if _deadline_exceeded(deadline):
            status = "runtime_exceeded"
            termination_reason = "main_worker_wall_time_exhausted"
            break
        absolute_turn = turns_already_used + main_turns + 1
        try:
            visible_history = _worker_visible_messages(history, tools)
            message = _chat_with_deadline(
                worker,
                visible_history,
                tools,
                seed=seed + absolute_turn - 1,
                deadline=deadline,
                max_tokens=8192,
            )
        except Exception as exc:
            provider_error = _redact_error_text(exc)[:500]
            if str(exc).startswith("context_capacity_exhausted:"):
                status = "context_capacity_exhausted"
                termination_reason = "context_capacity_exhausted"
            elif _deadline_exceeded(deadline):
                status = "runtime_exceeded"
                termination_reason = "main_worker_wall_time_exhausted"
            elif isinstance(exc, TimeoutError):
                status = "runtime_exceeded"
                termination_reason = "main_worker_gateway_timeout"
            else:
                status = "provider_failure"
                termination_reason = "main_worker_provider_failure"
            break

        main_turns += 1
        response_audit.append(_response_audit(message))
        assistant_message = _assistant_history_message(message)
        history.append(assistant_message)
        calls = message.get("tool_calls") or []
        content = str(message.get("content") or "").strip()
        if _response_was_truncated(message):
            _append_unexecuted_truncated_tool_results(
                history,
                calls,
            )
            continuation = {
                "role": "user",
                "content": (
                    "Your previous response reached the provider output limit "
                    "and is incomplete. No tool call from that response was "
                    "executed. Continue the same assignment from where it "
                    "stopped, using shorter steps if needed."
                ),
            }
            history.append(continuation)
            save_safe_progress()
            continue
        if not calls:
            if content:
                if arm == "controlled":
                    tool_events.append(
                        {
                            "event_id": _main_event_id(
                                absolute_turn, 0, "inner_loop_completion"
                            ),
                            "name": "inner_loop_completion",
                            "event_kind": "assistant_boundary",
                            "arguments": {},
                            "rc": None,
                            "result": content,
                            "tool_error": False,
                            "executed": False,
                        }
                    )
                    if main_turns >= turns_remaining:
                        status = "budget_exhausted"
                        termination_reason = (
                            "main_worker_budget_exhausted_at_inner_loop_completion"
                        )
                    else:
                        status = "inner_loop_completed"
                        termination_reason = "controlled_inner_loop_natural_completion"
                else:
                    status = "completed"
                    termination_reason = "natural_completion"
                final_answer = content
                break
            empty_responses += 1
            if empty_responses >= MAX_CONSECUTIVE_EMPTY_ASSISTANT_RESPONSES:
                status = "protocol_violation"
                termination_reason = "empty_assistant_response_limit_reached"
                break
            history.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response was empty. Continue the current assignment. "
                        "Use an attached tool if more work is needed; otherwise send a "
                        "non-empty ordinary response that reports the result or blocker "
                        "and ends this assignment."
                        if arm == "controlled"
                        else "Your previous response was empty. Continue working toward "
                        "the overall goal. Use an attached tool if more work is needed; "
                        "otherwise send a non-empty final response only when the overall "
                        "goal is complete or genuinely blocked."
                    ),
                }
            )
            save_safe_progress()
            continue
        empty_responses = 0
        if len(calls) != 1:
            error = "multiple_tool_calls_rejected"
            protocol_errors.append(error)
            for call_index, call in enumerate(calls):
                name = str((call.get("function") or {}).get("name") or "")
                result = (
                    "error: each main-worker response may contain at most one "
                    "tool call; no calls from this response were executed."
                )
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result,
                    }
                )
                tool_events.append(
                    {
                        "event_id": _main_event_id(absolute_turn, call_index, name),
                        "name": name,
                        "arguments": {},
                        "rc": None,
                        "result": result,
                        "tool_error": True,
                        "executed": False,
                    }
                )
            save_safe_progress()
            continue

        call = calls[0]
        name = str((call.get("function") or {}).get("name") or "")
        args, argument_error = _decode_tool_arguments(call)
        event_id = _main_event_id(absolute_turn, 0, name)
        if args is None:
            result = f"error: malformed tool arguments: {argument_error}"
            history.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
            )
            tool_events.append(
                {
                    "event_id": event_id,
                    "name": name,
                    "arguments": {},
                    "rc": None,
                    "result": result,
                }
            )
            save_safe_progress()
            continue
        if name == "final_report":
            # Accept the legacy completion spelling if a model emits it
            # unexpectedly; current tasks end with an ordinary response.
            final_answer = str(
                args.get("summary") or content or "Work slice complete."
            ).strip()
            result = "final_report acknowledged as a natural completion boundary"
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                }
            )
            tool_events.append(
                {
                    "event_id": event_id,
                    "name": name,
                    "event_kind": "assistant_boundary",
                    "arguments": args,
                    "rc": None,
                    "result": final_answer,
                    "tool_error": False,
                    "executed": False,
                }
            )
            if arm == "controlled":
                if main_turns >= turns_remaining:
                    status = "budget_exhausted"
                    termination_reason = (
                        "main_worker_budget_exhausted_at_inner_loop_completion"
                    )
                else:
                    status = "inner_loop_completed"
                    termination_reason = "controlled_inner_loop_natural_completion"
            else:
                status = "completed"
                termination_reason = "natural_completion"
            save_safe_progress()
            break
        if name not in REPO_TOOL_NAMES:
            result = (
                f"error: tool {name!r} is not available in the main-worker interface"
            )
            history.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
            )
            tool_events.append(
                {
                    "event_id": event_id,
                    "name": name,
                    "arguments": args,
                    "rc": None,
                    "result": result,
                    "tool_error": True,
                    "executed": False,
                }
            )
            protocol_errors.append(f"unavailable_tool_call:{name}")
            save_safe_progress()
            continue
        result, rc, tool_error = _exec_tool_event(
            name, args, sandbox, Path(workdir), mount_point, check_catalog=None
        )
        result = _nonempty_tool_result(name, result)
        repo_tool_calls += 1
        history.append(
            {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
        )
        tool_events.append(
            {
                "event_id": event_id,
                "name": name,
                "arguments": args,
                "rc": rc,
                "result": result,
                "tool_error": tool_error,
            }
        )
        if tool_error:
            # Repository-tool failures are observations in the worker's ReAct
            # loop. Preserve the error and let the worker correct or retry it;
            # provider, deadline, and protocol failures terminate elsewhere.
            save_safe_progress()
            continue
        save_safe_progress()

    if status == "running":
        status = "budget_exhausted"
        termination_reason = "main_worker_budget_exhausted"
    return {
        "arm": arm,
        "control_decision": control_decision,
        "status": status,
        "termination_reason": termination_reason,
        "messages": history,
        "main_worker_turns": main_turns,
        "repo_tool_calls": repo_tool_calls,
        "tool_events": tool_events,
        "model_response_audit": response_audit,
        "final_answer": final_answer,
        "provider_error": provider_error,
        "protocol_errors": protocol_errors,
    }


def run_forked_reporter(
    worker: Any,
    sandbox: Any,
    workdir: Path,
    main_messages: list[dict[str, Any]],
    reporter_prompt: str,
    *,
    reporter_system_prompt: str | None = None,
    max_reporter_turns: int = 50,
    seed: int = 0,
    mount_point: str = "/work",
    wall_time_limit_sec: int | float | None = None,
    report_validator: Callable[[dict], list[str]] | None = None,
    report_event_validator: Callable[[dict, list[dict[str, Any]]], list[str]]
    | None = None,
    initial_state: dict[str, Any] | None = None,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Report on a quoted worker transcript in a fresh read-only model session."""

    if max_reporter_turns <= 0:
        raise ValueError("max_reporter_turns must be positive")
    if (
        not isinstance(reporter_system_prompt, str)
        or not reporter_system_prompt.strip()
    ):
        reporter_system_prompt = (
            "Prepare a neutral factual report about the quoted coding conversation. "
            "Do not continue the coding task or modify the repository."
        )
    initial_state = copy.deepcopy(initial_state or {})
    default_messages = [
        {"role": "system", "content": reporter_system_prompt},
        {"role": "user", "content": reporter_prompt},
    ]
    messages = copy.deepcopy(initial_state.get("messages") or default_messages)
    if not isinstance(messages, list) or len(messages) < 2:
        raise ValueError("reporter recovery state has no conversation")
    next_turn_index = int(initial_state.get("next_turn_index") or 0)
    if not 0 <= next_turn_index <= max_reporter_turns:
        raise ValueError("reporter recovery turn index is outside the budget")
    pending_response = copy.deepcopy(initial_state.get("pending_response"))
    if pending_response is not None and not isinstance(pending_response, dict):
        raise ValueError("reporter pending response must be an object")
    base_wall_time_sec = float(initial_state.get("wall_time_sec") or 0.0)
    progress_started = time.monotonic()
    remaining_wall_time = (
        max(0.0, float(wall_time_limit_sec) - base_wall_time_sec)
        if wall_time_limit_sec is not None and float(wall_time_limit_sec) > 0
        else None
    )
    deadline = (
        time.monotonic() + remaining_wall_time
        if remaining_wall_time is not None
        else None
    )
    events: list[dict[str, Any]] = copy.deepcopy(initial_state.get("events") or [])
    audits: list[dict[str, Any]] = copy.deepcopy(initial_state.get("audits") or [])
    report = copy.deepcopy(initial_state.get("report"))
    errors: list[str] = copy.deepcopy(initial_state.get("errors") or [])
    tool_errors: list[str] = copy.deepcopy(initial_state.get("tool_errors") or [])

    def save_progress(next_step: str) -> None:
        if progress_sink is None:
            return
        progress_sink(
            {
                "next_step": next_step,
                "next_turn_index": next_turn_index,
                "messages": copy.deepcopy(messages),
                "pending_response": copy.deepcopy(pending_response),
                "events": copy.deepcopy(events),
                "audits": copy.deepcopy(audits),
                "report": copy.deepcopy(report),
                "errors": copy.deepcopy(errors),
                "tool_errors": copy.deepcopy(tool_errors),
                "wall_time_sec": round(
                    base_wall_time_sec + time.monotonic() - progress_started, 6
                ),
            }
        )

    provider_failure: dict[str, str] | None = None
    while report is None and next_turn_index < max_reporter_turns:
        if _deadline_exceeded(deadline):
            errors.append("reporter_wall_time_exhausted")
            break
        if pending_response is None:
            save_progress("call_model")
            try:
                reporter_max_tokens = _reporter_max_output_tokens(
                    worker,
                    messages,
                )
                message = _chat_with_deadline(
                    worker,
                    messages,
                    REPORTER_TOOLS,
                    seed=seed + next_turn_index,
                    deadline=deadline,
                    # Keep the preregistered 8192-token maximum. Near the
                    # fail-closed context boundary, request only the remaining
                    # output capacity instead of rejecting the reporter fork
                    # before the gateway is called.
                    max_tokens=reporter_max_tokens,
                )
            except Exception as exc:
                provider_failure = {
                    "error_type": type(exc).__name__,
                    "redacted_error": _redact_error_text(str(exc))[:500],
                }
                if str(exc).startswith("context_capacity_exhausted:"):
                    errors.append("reporter_context_capacity_exhausted")
                elif _deadline_exceeded(deadline):
                    errors.append("reporter_wall_time_exhausted")
                elif isinstance(exc, TimeoutError):
                    errors.append("reporter_gateway_timeout")
                else:
                    errors.append("reporter_provider_failure")
                break
            audits.append(_response_audit(message))
            assistant_message = _assistant_history_message(message)
            messages.append(assistant_message)
            pending_response = copy.deepcopy(message)
            save_progress("process_response")
        message = pending_response
        assert isinstance(message, dict)
        calls = message.get("tool_calls") or []
        if _response_was_truncated(message):
            _append_unexecuted_truncated_tool_results(
                messages,
                calls,
            )
            continuation = {
                "role": "user",
                "content": (
                    "Your previous response reached the provider output limit "
                    "and is incomplete. No tool call from that response was "
                    "executed. Continue the same factual reporting task in "
                    "shorter steps, then submit one complete round_report."
                ),
            }
            messages.append(continuation)
            pending_response = None
            next_turn_index += 1
            save_progress("call_model")
            continue
        if len(calls) != 1:
            if calls:
                for call in calls:
                    feedback = {
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": (
                            "error: reporter responses must contain exactly one "
                            "tool call; no calls were executed"
                        ),
                    }
                    messages.append(feedback)
            else:
                feedback = {
                    "role": "user",
                    "content": (
                        "Use one reporter inspection tool, or call round_report "
                        "as the sole tool call."
                    ),
                }
                messages.append(feedback)
            pending_response = None
            next_turn_index += 1
            save_progress("call_model")
            continue
        call = calls[0]
        name = str((call.get("function") or {}).get("name") or "")
        args, argument_error = _decode_tool_arguments(call)
        if args is None:
            feedback = {
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": f"error: malformed reporter tool arguments: {argument_error}",
            }
            messages.append(feedback)
            pending_response = None
            next_turn_index += 1
            save_progress("call_model")
            continue
        if name == "round_report":
            candidate_errors = report_validator(args) if report_validator else []
            if report_event_validator is not None:
                candidate_errors.extend(report_event_validator(args, events))
            if candidate_errors:
                feedback = {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": "error: round_report validation failed: "
                    + json.dumps(candidate_errors, ensure_ascii=False),
                }
                messages.append(feedback)
                correction = {
                    "role": "user",
                    "content": _round_report_correction_prompt(candidate_errors),
                }
                messages.append(correction)
                pending_response = None
                next_turn_index += 1
                save_progress("call_model")
                continue
            report = args
            pending_response = None
            next_turn_index += 1
            save_progress("completed")
            break
        safe_name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")
        event_id = f"reporter_event:{next_turn_index + 1}:0:{safe_name}"
        if name in {
            "read_file",
            "view_file",
            "list_files",
            "search",
            "search_text",
            "get_status",
            "get_diff",
        }:
            result, rc, tool_error = _exec_tool_event(
                name, args, sandbox, Path(workdir), mount_point, check_catalog=None
            )
        else:
            result, rc, tool_error = (
                f"error: reporter tool {name!r} is not available",
                None,
                True,
            )
        result = _nonempty_tool_result(name, result)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            }
        )
        events.append(
            {
                "event_id": event_id,
                "assistant_text": str(message.get("content") or ""),
                "name": name,
                "arguments": args,
                "rc": rc,
                "result": result,
                "tool_error": tool_error,
            }
        )
        if tool_error:
            tool_errors.append(f"reporter_tool_error:{name}")
        pending_response = None
        next_turn_index += 1
        save_progress("call_model")

    if report is None and not errors:
        errors.append("reporter_turn_budget_exhausted_without_round_report")
    return {
        "status": "completed" if report is not None and not errors else "failed",
        "report": report,
        "reporter_turns": len(audits),
        "tool_events": events,
        "exposed_tool_names": sorted(
            tool["function"]["name"] for tool in REPORTER_TOOLS
        ),
        "model_response_audit": audits,
        "errors": errors,
        "provider_failure": provider_failure,
        "tool_errors": tool_errors,
        # The canonical Worker transcript and deterministic Reporter prompt are
        # already available; retain only messages unique to this Reporter call.
        "reporter_messages": messages[2:],
        "worker_transcript_message_count": len(main_messages),
        "wall_time_sec": round(
            base_wall_time_sec + time.monotonic() - progress_started, 6
        ),
    }
