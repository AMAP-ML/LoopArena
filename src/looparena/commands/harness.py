#!/usr/bin/env python3
"""Run a LoopArena controlled or no-control episode.

This module implements the public execution graphs:

* no-control: one deterministic start input, uninterrupted main-worker ReAct;
* controlled: natural inner-loop completion -> read-only reporter ->
  deterministic packet -> tested controller -> one user turn in the same
  main-worker conversation.

SCBench evaluates a sealed workspace after the solve sandbox stops. BeyondSWE
uses Harbor's shared-verifier boundary: after every model call has ended, it
seals the workspace, injects private tests into the still-live solve container,
and evaluates there before teardown.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from looparena.paths import repository_root

ROOT = repository_root()

from looparena import runtime as _worker
from looparena.harness import protocol as P
from looparena.harness import rendering as R
from looparena.harness import validation as V
from looparena.harness.controller import CONTROLLER_MAX_OUTPUT_TOKENS
from looparena.harness.continuous_session import (
    CONTROL_CHANNEL_WALL_TIME_LIMIT_SEC,
    CONTROL_CYCLE_LIMIT,
    MAIN_WORKER_TURN_LIMIT,
    REPORTER_TURN_LIMIT,
    ContinuousHarnessSession,
)
from looparena.harness.evaluator_protocol import (
    classify_infrastructure_validity,
    derive_task_outcome,
    validate_evaluator_plan,
)
from looparena.harness.recovery import (
    atomic_write_json,
    read_checkpoint,
)
from looparena.harness.solve_preflight import build_solve_environment_preflight
from looparena.harness.transcript import (
    derive_public_tool_events,
)
from looparena.harness.validation import (
    capacity_policy_identity,
    require_task_text,
)
from looparena.runtime import llm as _llm
from looparena.runtime import sandbox as _sandbox
from looparena.runtime.controller_client import (
    ControllerChatClient,
)
from looparena.runtime.non_adaptive_fixed_controller import (
    NonAdaptiveFixedController,
)
from looparena.runtime.source_identity import capture_harness_identity

DEFAULT_GATEWAY = _llm.default_worker_base_url()
DEFAULT_MODEL = "qwen3.7-plus"


class _RunSignalInterruption(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(f"received signal {signum}")
        self.signum = int(signum)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            )


def _append_jsonl_durable(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _run_solve_runtime_setup(
    sandbox: Any,
    commands: list[str],
    timeout_sec: int,
) -> dict[str, Any]:
    """Restore the public source runtime before any model is called."""

    original_timeout = int(getattr(sandbox, "exec_timeout", timeout_sec))
    sandbox.exec_timeout = timeout_sec
    try:
        results = sandbox.setup(commands) if commands else []
    finally:
        sandbox.exec_timeout = original_timeout
    receipt = {
        "status": (
            "passed" if all(int(row.get("rc", 1)) == 0 for row in results) else "failed"
        ),
        "commands": commands,
        "results": results,
        "runtime_mount_point": str(getattr(sandbox, "runtime_mount_point", "")),
        "runtime_venv_relative_path": getattr(
            sandbox, "runtime_venv_relative_path", None
        ),
    }
    return receipt


def _controller_row(result: Any, response_message_index: int) -> dict[str, Any]:
    return {
        "contract": result.contract,
        "response_message_index": response_message_index,
        "validation_errors": result.validation_errors,
        "failure_kind": result.failure_kind,
        "failure_diagnostics": result.failure_diagnostics,
        "model_call_audits": result.model_call_audits,
        "context_compaction_audit": result.context_compaction_audit,
    }


def _token_usage_summary(audits: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = completion = total = 0
    reported = 0
    cache_reported = 0
    cached_prompt = cache_creation = cache_read = 0
    gateway_attempts = 0
    for audit in audits:
        usage = audit.get("usage") if isinstance(audit, dict) else None
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            prompt += int(usage.get("prompt_tokens") or 0)
            completion += int(usage.get("completion_tokens") or 0)
            total += int(usage.get("total_tokens") or 0)
            reported += 1
            cache_values = (
                usage.get("cached_prompt_tokens"),
                usage.get("cache_creation_input_tokens"),
                usage.get("cache_read_input_tokens"),
            )
            if any(
                isinstance(value, int) and not isinstance(value, bool)
                for value in cache_values
            ):
                cache_reported += 1
                cached_prompt += int(cache_values[0] or 0)
                cache_creation += int(cache_values[1] or 0)
                cache_read += int(cache_values[2] or 0)
        attempts = audit.get("gateway_attempts") if isinstance(audit, dict) else None
        if isinstance(attempts, int) and not isinstance(attempts, bool):
            gateway_attempts += attempts
    return {
        "request_count": len(audits),
        "usage_reported_request_count": reported,
        "prompt_tokens": prompt if reported else None,
        "completion_tokens": completion if reported else None,
        "total_tokens": total if reported else None,
        "cache_usage_reported_request_count": cache_reported,
        "cached_prompt_tokens": cached_prompt if cache_reported else None,
        "cache_creation_input_tokens": cache_creation if cache_reported else None,
        "cache_read_input_tokens": cache_read if cache_reported else None,
        "gateway_attempts": gateway_attempts if gateway_attempts else None,
    }


def _recovery_runtime_identity(
    *,
    worker: Any,
    main_sandbox: Any,
    controller: Any | None,
    worker_wall_time_sec: int | None,
    reporter_wall_time_sec: int | None,
) -> dict[str, Any]:
    """Capture every runtime choice that may affect post-checkpoint behavior."""

    controller_provider_kind = str(
        getattr(controller, "provider_kind", "model") if controller is not None else ""
    )
    controller_transport = str(
        getattr(controller, "transport", "") if controller is not None else ""
    )
    return {
        "worker": {
            "model": str(getattr(worker, "model", "")),
            "transport": str(getattr(worker, "transport", "")),
            "streaming": bool(getattr(worker, "streaming", False)),
            "credential_profile_id": str(getattr(worker, "credential_profile_id", "")),
            "context_capacity_utf8_bytes": int(
                getattr(
                    worker,
                    "context_capacity_utf8_bytes",
                    _llm.DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES,
                )
            ),
        },
        "sandbox": {
            "image": str(getattr(main_sandbox, "image", "")),
            "image_identity": getattr(main_sandbox, "image_identity", None),
            "network": str(getattr(main_sandbox, "network", "")),
            "mount_point": str(getattr(main_sandbox, "mount_point", "")),
            "workspace_read_only": bool(
                getattr(main_sandbox, "workspace_read_only", False)
            ),
            "repository_command_timeout_sec": int(
                getattr(main_sandbox, "exec_timeout", 0)
            ),
        },
        "timeout_policy": {
            "main_worker_wall_time_sec": worker_wall_time_sec,
            "reporter_wall_time_sec_per_call": reporter_wall_time_sec,
            "gateway_request_timeout_sec": int(getattr(worker, "timeout_sec", 0)),
            "gateway_requests_bounded_by_remaining_deadline": True,
        },
        "controller": (
            {
                "provider_kind": controller_provider_kind,
                "model": str(getattr(controller, "model", "")),
                "transport": controller_transport,
                "credential_profile_id": str(
                    getattr(controller, "credential_profile_id", "")
                ),
            }
            if controller is not None
            else None
        ),
        "retry_policy": {
            "gateway_attempt_limit_per_credential": _llm.WORKER_GATEWAY_ATTEMPT_LIMIT,
            "explicit_fallback_credential_enabled": bool(
                os.environ.get(_llm.EXPLICIT_FALLBACK_API_KEY_ENV)
            ),
            "controller_outer_attempt_limit": (
                int(getattr(controller, "max_retries", 0))
                if controller is not None
                and controller_provider_kind == "model"
                and controller_transport == "gateway"
                else 0
            ),
            "controller_gateway_attempt_limit_per_credential": (
                _llm.WORKER_GATEWAY_ATTEMPT_LIMIT
                if controller is not None
                and controller_provider_kind == "model"
                and controller_transport == "gateway"
                else 0
            ),
            "sdk_automatic_retries": 0,
        },
    }


def build_start_context(
    *,
    task: str,
    sample_id: str,
) -> dict[str, Any]:
    """Build deterministic shared start context; it is not a reporter packet."""

    return {
        "sample_id": sample_id,
        "task": require_task_text(task),
        "current_state": "The repository is restored at the declared starting state.",
        "current_objective": require_task_text(task),
        "acceptance_criteria": [],
        "acceptance_criteria_provenance": [],
        "controller_state": {},
        "remaining_uncertainty": [],
        "reported_blockers": [],
        "protected_invariants": [],
        "allowed_control_decisions": list(P.CONTROL_DECISIONS),
        "worker_tool_policy": [
            "The worker may inspect and edit repository files and run repository commands.",
            "Advance and verify use the same editable repository tool surface.",
        ],
        "work_log": [],
        "fact_cards": [],
        "scope_cards": [],
        "budget": {
            "budget_unit": "main_worker_react_turn",
            "max_inner_react_turns_total": MAIN_WORKER_TURN_LIMIT,
            "used_inner_react_turns": 0,
            "remaining_inner_react_turns": MAIN_WORKER_TURN_LIMIT,
        },
    }


def run_episode(
    *,
    arm: str,
    task: str,
    workspace: Path,
    out_dir: Path,
    worker: Any,
    main_sandbox: Any,
    reporter_sandbox_factory: Any | None,
    controller: Any | None,
    sample_id: str,
    seed: int,
    worker_wall_time_sec: int | None,
    reporter_wall_time_sec: int | None,
    serialized_messages: list[dict[str, Any]] | None = None,
    solve_environment_preflight: dict[str, Any] | None = None,
    solve_runtime_setup: dict[str, Any] | None = None,
    recovery_checkpoint: dict[str, Any] | None = None,
    harness_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace).resolve()
    out_dir = Path(out_dir).resolve()
    start_mode = "bootstrap_contract_start"
    if (
        out_dir == workspace
        or out_dir in workspace.parents
        or workspace in out_dir.parents
    ):
        raise ValueError("workspace and out_dir must be disjoint")
    resume_interrupted = recovery_checkpoint is not None
    if out_dir.exists() and not resume_interrupted:
        existing_names = {path.name for path in out_dir.iterdir()}
        if existing_names - {"attempt_state.json"}:
            raise ValueError("out_dir must be empty")
    out_dir.mkdir(parents=True, exist_ok=True)
    if serialized_messages is not None:
        if not isinstance(serialized_messages, list) or not serialized_messages:
            raise ValueError("serialized_messages must be a non-empty message list")
        serialized_messages = json.loads(json.dumps(serialized_messages))
        conversation_errors = V.validate_public_conversation(serialized_messages)
        if conversation_errors:
            raise ValueError(
                "serialized_public_conversation_invalid:"
                + ";".join(conversation_errors)
            )
        first_message = serialized_messages[0]
        if first_message.get("role") != "system":
            raise ValueError("serialized context must start with a system message")
        # The system prompt is runtime policy, not sample evidence. Upgrade only
        # that message at load time and preserve the historical conversation tail.
        serialized_messages[0] = {
            "role": "system",
            "content": R.WORKER_SYSTEM_PROMPT,
        }
        # Preserve the model-visible Type II prefix exactly after replacing its
        # runtime-owned system message.
    start_context = build_start_context(
        task=task,
        sample_id=sample_id,
    )
    conversation = _worker.WorkerConversation(
        worker,
        main_sandbox,
        workspace,
        system_prompt=R.WORKER_SYSTEM_PROMPT,
        mount_point=getattr(main_sandbox, "mount_point", "/work"),
    )
    resume = serialized_messages is not None
    initial_prefix_message_count = (
        len(serialized_messages)
        if serialized_messages is not None
        else int(recovery_checkpoint.get("initial_prefix_message_count") or 2)
        if recovery_checkpoint is not None
        else 2
    )
    if serialized_messages is not None:
        conversation.messages = json.loads(json.dumps(serialized_messages))

    checkpoint_path = out_dir / "recovery_checkpoint.json"
    checkpoint_static = {
        "arm": arm,
        "sample_id": sample_id,
        "seed": seed,
        "start_mode": start_mode,
        "runtime_identity": _recovery_runtime_identity(
            worker=worker,
            main_sandbox=main_sandbox,
            controller=controller,
            worker_wall_time_sec=worker_wall_time_sec,
            reporter_wall_time_sec=reporter_wall_time_sec,
        ),
        "initial_prefix_message_count": initial_prefix_message_count,
    }
    if recovery_checkpoint is not None:
        for key in ("arm", "sample_id", "seed", "start_mode"):
            expected = checkpoint_static[key]
            if recovery_checkpoint.get(key) != expected:
                raise ValueError(f"recovery checkpoint identity mismatch: {key}")
        if recovery_checkpoint.get("safe_to_resume") is not True:
            raise ValueError("recovery checkpoint is not at a safe worker boundary")
        recovery_cycle = recovery_checkpoint.get("cycle_index")
        if not isinstance(recovery_cycle, int) or recovery_cycle < 0:
            raise ValueError("recovery checkpoint cycle index is invalid")
        if arm == "controlled" and not isinstance(
            recovery_checkpoint.get("controlled_session_state"), dict
        ):
            raise ValueError("controlled recovery checkpoint is missing session state")
        messages = recovery_checkpoint.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("recovery checkpoint conversation is missing")
        conversation.messages = json.loads(json.dumps(messages))
        conversation.total_model_calls = int(
            recovery_checkpoint.get("main_worker_turns") or 0
        )
        conversation.total_repo_actions = int(
            recovery_checkpoint.get("repo_tool_calls") or 0
        )
    session_resume = (
        recovery_checkpoint.get("controlled_session_state")
        if recovery_checkpoint is not None and arm == "controlled"
        else None
    )

    def save_main_progress(cycle_index: int, progress: dict[str, Any]) -> None:
        payload = {
            **checkpoint_static,
            "phase": progress.get("phase") or "call_worker",
            "cycle_index": cycle_index,
            "safe_to_resume": True,
            "messages": progress.get("messages") or [],
            "main_worker_turns": progress.get("main_worker_turns"),
            "repo_tool_calls": progress.get("repo_tool_calls"),
            "current_slice_main_worker_turns": progress.get(
                "current_slice_main_worker_turns"
            ),
            "current_slice_repo_tool_calls": progress.get(
                "current_slice_repo_tool_calls"
            ),
            "control_decision": progress.get("control_decision"),
            "control_events": progress.get("control_events") or [],
            "controlled_session_state": progress.get("controlled_session_state"),
            "solve_environment_preflight": solve_environment_preflight,
            "tool_events": progress.get("tool_events") or [],
            "model_response_audit": progress.get("model_response_audit") or [],
            "consecutive_empty_responses": progress.get("consecutive_empty_responses")
            or 0,
            "protocol_errors": progress.get("protocol_errors") or [],
            "main_worker_wall_time_sec": progress.get("wall_time_sec") or 0.0,
        }
        atomic_write_json(checkpoint_path, payload)

    initial_main_events = (
        derive_public_tool_events(
            serialized_messages,
            event_namespace="prefix_event",
        )
        if serialized_messages is not None and arm == "controlled"
        else []
    )
    session = ContinuousHarnessSession(
        arm=arm,
        start_context=start_context,
        initial_control_packet=(
            session_resume.get("current_packet")
            if arm == "controlled" and session_resume is not None
            else None
        ),
        initial_main_events=initial_main_events,
        conversation=conversation,
        main_progress_sink=save_main_progress,
        controller=controller,
        reporter_sandbox_factory=reporter_sandbox_factory,
        max_main_worker_turns=MAIN_WORKER_TURN_LIMIT,
        max_reporter_turns=REPORTER_TURN_LIMIT,
        seed=seed,
        wall_time_limit_sec=worker_wall_time_sec,
        reporter_wall_time_limit_sec=reporter_wall_time_sec,
        resume_main_slice_prefix=(
            {
                "main_worker_turns": recovery_checkpoint.get(
                    "current_slice_main_worker_turns"
                )
                if recovery_checkpoint.get("current_slice_main_worker_turns")
                is not None
                else recovery_checkpoint.get("main_worker_turns"),
                "repo_tool_calls": recovery_checkpoint.get(
                    "current_slice_repo_tool_calls"
                )
                if recovery_checkpoint.get("current_slice_repo_tool_calls") is not None
                else recovery_checkpoint.get("repo_tool_calls"),
                "tool_events": recovery_checkpoint.get("tool_events") or [],
                "model_response_audit": recovery_checkpoint.get("model_response_audit")
                or [],
                "consecutive_empty_responses": recovery_checkpoint.get(
                    "consecutive_empty_responses"
                )
                or 0,
                "protocol_errors": recovery_checkpoint.get("protocol_errors") or [],
                "wall_time_sec": recovery_checkpoint.get("main_worker_wall_time_sec")
                or 0.0,
            }
            if recovery_checkpoint is not None
            else None
        ),
        resume_control_events=(
            recovery_checkpoint.get("control_events") or []
            if recovery_checkpoint is not None
            else None
        ),
        resume_state=session_resume,
    )
    result = session.run(
        resume_existing_conversation=resume or resume_interrupted,
        resume_in_progress=resume_interrupted,
    )
    resumable_provider_failure = result.termination_reason in {
        "main_worker_provider_failure",
        "reporter_provider_failure",
        "controller_provider_failure",
    }
    if checkpoint_path.exists() and not resumable_provider_failure:
        checkpoint_path.unlink()
    controller_rows = [
        _controller_row(row, 2 * (index + 1))
        for index, row in enumerate(result.controller_results)
    ]
    _write_json(out_dir / "start_context.json", start_context)
    if solve_environment_preflight is not None:
        _write_json(
            out_dir / "solve_environment_preflight.json",
            solve_environment_preflight,
        )
    if solve_runtime_setup is not None:
        _write_json(
            out_dir / "solve_runtime_setup.json",
            solve_runtime_setup,
        )
    _write_jsonl(out_dir / "transcript.jsonl", conversation.messages)
    _write_jsonl(out_dir / "main_worker_slices.jsonl", result.main_worker_slices)
    _write_jsonl(out_dir / "reporter_runs.jsonl", result.reporter_runs)
    _write_jsonl(out_dir / "packets.jsonl", result.packets)
    _write_jsonl(out_dir / "control_events.jsonl", result.control_events)
    _write_jsonl(out_dir / "controller_results.jsonl", controller_rows)
    _write_jsonl(
        out_dir / "controller_transcript.jsonl", result.controller_conversation
    )
    main_audits = [
        audit
        for main_slice in result.main_worker_slices
        for audit in main_slice.get("model_response_audit") or []
    ]
    reporter_audits = [
        audit
        for reporter_run in result.reporter_runs
        for audit in reporter_run.get("model_response_audit") or []
    ]
    controller_audits = [
        call.get("response_audit") or {}
        for controller_result in result.controller_results
        for call in controller_result.model_call_audits
        if isinstance(call, dict) and call.get("response_audit")
    ]
    controller_provider_kind = (
        str(getattr(controller, "provider_kind", "model"))
        if controller is not None
        else ""
    )
    fixed_control = controller_provider_kind == "non-adaptive-fixed"
    controller_transport = (
        str(getattr(controller, "transport", "")) if controller is not None else ""
    )
    worker_uses_provider_sampling = _llm.provider_family(
        str(getattr(worker, "model", ""))
    ) in {"gpt", "claude"}
    manifest = {
        "arm": arm,
        "status": result.status,
        "termination_reason": result.termination_reason,
        "terminal_action": result.terminal_action,
        "infrastructure_validity": classify_infrastructure_validity(
            result.status, result.termination_reason
        ),
        "sample_id": sample_id,
        "start_mode": start_mode,
        "seed": seed,
        "initial_prefix_message_count": initial_prefix_message_count,
        "main_worker_turn_budget": MAIN_WORKER_TURN_LIMIT,
        "main_worker_turns": result.main_worker_turns,
        "main_worker_repo_tool_calls": result.main_worker_repo_tool_calls,
        "main_worker_wall_time_elapsed_sec": result.main_worker_wall_time_sec,
        "reporter_turn_limit_per_call": 0 if fixed_control else REPORTER_TURN_LIMIT,
        "control_cycle_limit": CONTROL_CYCLE_LIMIT,
        "control_channel_budget_policy": {
            "max_control_cycles": CONTROL_CYCLE_LIMIT,
            "reporter_turns_per_call": 0 if fixed_control else REPORTER_TURN_LIMIT,
            "max_reporter_turns_total": (
                0 if fixed_control else CONTROL_CYCLE_LIMIT * REPORTER_TURN_LIMIT
            ),
            "max_control_channel_wall_time_sec": CONTROL_CHANNEL_WALL_TIME_LIMIT_SEC,
        },
        "control_channel_usage": {
            "control_cycles": result.controller_calls,
            "reporter_turns": result.reporter_turns,
            "wall_time_sec": result.control_channel_wall_time_sec,
        },
        "reporter_turns": result.reporter_turns,
        "controller_calls": result.controller_calls,
        "packet_count": len(result.packets),
        "report_count": sum(
            isinstance(run.get("report"), dict) for run in result.reporter_runs
        ),
        "compute_accounting": {
            "main_worker": {
                "react_turns": result.main_worker_turns,
                "wall_time_sec": result.main_worker_wall_time_sec,
                "tokens": _token_usage_summary(main_audits),
            },
            "controlled_only": {
                "reporter_turns": result.reporter_turns,
                "reporter_wall_time_sec": round(
                    sum(
                        float(row.get("wall_time_sec") or 0.0)
                        for row in result.reporter_runs
                    ),
                    6,
                ),
                "reporter_tokens": _token_usage_summary(reporter_audits),
                "controller_calls": result.controller_calls,
                "controller_wall_time_sec": round(
                    sum(
                        float(event.get("controller_wall_time_sec") or 0.0)
                        for event in result.control_events
                    ),
                    6,
                ),
                "controller_tokens": _token_usage_summary(controller_audits),
                "control_cycles": result.controller_calls,
            },
        },
        "execution_graph": (
            "one_start_then_uninterrupted_main_worker"
            if arm == "no-control"
            else "natural_inner_loop_fixed_controller_contract"
            if fixed_control
            else "natural_inner_loop_reporter_packet_controller_contract"
        ),
        "control_channel_kind": (
            "same_harness_no_control"
            if arm == "no-control"
            else "non_adaptive_fixed_goal_control"
            if controller_provider_kind == "non-adaptive-fixed"
            else "public_packet_control"
        ),
        "capacity_policy": capacity_policy_identity(),
        "budget_policy": {
            "main_worker_react_turns": MAIN_WORKER_TURN_LIMIT,
            "reporter_react_turns_per_call": (
                0 if fixed_control else REPORTER_TURN_LIMIT
            ),
            "control_cycles": CONTROL_CYCLE_LIMIT,
            "main_worker_max_output_tokens_per_request": 8192,
            "reporter_max_output_tokens_per_request": None if fixed_control else 8192,
            "controller_max_output_tokens_per_request": (
                None
                if controller_provider_kind == "non-adaptive-fixed"
                else CONTROLLER_MAX_OUTPUT_TOKENS
            ),
        },
        "sampling_policy": {
            "shared_main_worker": {
                "provider_sampling_mode": (
                    "provider_default" if worker_uses_provider_sampling else "explicit"
                ),
                "temperature": None if worker_uses_provider_sampling else 0.7,
                "max_output_tokens": 8192,
                "thinking_mode": "provider_default",
                "context_capacity_utf8_bytes": int(
                    getattr(
                        worker,
                        "context_capacity_utf8_bytes",
                        _llm.DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES,
                    )
                ),
                "seed_schedule": (
                    None
                    if worker_uses_provider_sampling
                    else "run_seed_plus_absolute_main_worker_turn_minus_one"
                ),
            },
            "controlled_only_reporter": None
            if fixed_control
            else {
                "provider_sampling_mode": (
                    "provider_default" if worker_uses_provider_sampling else "explicit"
                ),
                "temperature": None if worker_uses_provider_sampling else 0.7,
                "max_output_tokens": 8192,
                "thinking_mode": "provider_default",
                "context_capacity_utf8_bytes": int(
                    getattr(
                        worker,
                        "context_capacity_utf8_bytes",
                        _llm.DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES,
                    )
                ),
                "seed_schedule": (
                    None
                    if worker_uses_provider_sampling
                    else "run_seed_plus_100000_plus_cycle_times_50_plus_reporter_turn"
                ),
            },
            "controlled_only_controller": (
                {
                    "provider_kind": "non-adaptive-fixed",
                    "sampling": None,
                    "packet_use": "bound_but_not_inspected",
                    "policy_id": str(getattr(controller, "policy_id", "")),
                    "worker_prompt_renderer": "standard_controlled_continuation",
                    "decision_policy": (
                        "repeat_fixed_goal_until_exact_worker_completion_signal"
                    ),
                    "termination_policy": (
                        "stop_only_after_exact_final_line_goal_complete"
                    ),
                    "worker_output_protocol": "exact_final_line:Goal complete.",
                }
                if controller_provider_kind == "non-adaptive-fixed"
                else {
                    "provider_sampling_mode": (
                        "provider_default"
                        if getattr(controller, "temperature", None) is None
                        else "explicit"
                    ),
                    "temperature": (
                        float(controller.temperature)
                        if getattr(controller, "temperature", None) is not None
                        else None
                    ),
                    "max_output_tokens": CONTROLLER_MAX_OUTPUT_TOKENS,
                    "thinking_mode": str(
                        getattr(controller, "thinking_mode", "provider_default")
                    ),
                    "context_capacity_utf8_bytes": int(
                        getattr(
                            getattr(controller, "client", None),
                            "context_capacity_utf8_bytes",
                            _llm.DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES,
                        )
                    ),
                    "seed_schedule": (
                        "run_seed_plus_controller_call_index"
                        if bool(getattr(controller, "seed_supported", True))
                        else None
                    ),
                }
                if controller is not None
                else None
            ),
        },
        "schedule_policy": {
            "outer_control": "natural_inner_loop_completion_driven",
            "no_control": "one_start_then_uninterrupted",
            "controlled_bootstrap": "deterministic_verify_then_natural_completion",
            "bootstrap_contract_start": "deterministic_verify_then_natural_completion",
            "control_cycle_limit": CONTROL_CYCLE_LIMIT,
        },
        "retry_policy": {
            "worker_gateway_attempt_limit_per_request": _llm.WORKER_GATEWAY_ATTEMPT_LIMIT,
            "controller_outer_attempt_limit": (
                int(getattr(controller, "max_retries", 0))
                if controller is not None
                and controller_provider_kind == "model"
                and controller_transport == "gateway"
                else 0
            ),
            "controller_gateway_attempt_limit_per_outer_attempt": (
                _llm.WORKER_GATEWAY_ATTEMPT_LIMIT
                if controller is not None
                and controller_provider_kind == "model"
                and controller_transport == "gateway"
                else 0
            ),
            "sdk_automatic_retries": 0,
        },
        "official_evaluator_ran_during_solve": False,
        "solve_environment_preflight": solve_environment_preflight,
        "solve_runtime_setup": solve_runtime_setup,
        "task_outcome": derive_task_outcome(
            {
                "main_worker_turns": result.main_worker_turns,
                "main_worker_turn_budget": MAIN_WORKER_TURN_LIMIT,
            },
            None,
        ),
        "worker": {
            "model": str(getattr(worker, "model", "")),
            "transport": str(getattr(worker, "transport", "")),
            "streaming": bool(getattr(worker, "streaming", False)),
            "credential_profile_id": str(getattr(worker, "credential_profile_id", "")),
            "gateway_sdk_max_retries": 0,
            "context_capacity_utf8_bytes": int(
                getattr(
                    worker,
                    "context_capacity_utf8_bytes",
                    _llm.DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES,
                )
            ),
        },
        "controller": (
            {
                "provider_kind": str(getattr(controller, "provider_kind", "model")),
                "model": str(getattr(controller, "model", "")),
                "transport": str(getattr(controller, "transport", "")),
                "credential_profile_id": str(
                    getattr(controller, "credential_profile_id", "")
                ),
                "explicit_max_retries": int(getattr(controller, "max_retries", 0)),
            }
            if controller is not None
            else None
        ),
        "runtime_environment": {
            "image": str(getattr(main_sandbox, "image", "")),
            "image_identity": getattr(main_sandbox, "image_identity", None),
            "network": str(getattr(main_sandbox, "network", "")),
            "mount_point": str(getattr(main_sandbox, "mount_point", "")),
            "workspace_read_only": bool(
                getattr(main_sandbox, "workspace_read_only", False)
            ),
        },
        "timeout_policy": {
            "main_worker_wall_time_sec": worker_wall_time_sec,
            "reporter_wall_time_sec_per_call": reporter_wall_time_sec,
            "gateway_request_timeout_sec": int(getattr(worker, "timeout_sec", 0)),
            "repository_command_timeout_sec": int(
                getattr(main_sandbox, "exec_timeout", 0)
            ),
            "gateway_requests_bounded_by_remaining_deadline": True,
        },
        "harness_identity": harness_identity or capture_harness_identity(ROOT),
        "artifacts": {
            "start_context": "start_context.json",
            "transcript": "transcript.jsonl",
            "main_worker_slices": "main_worker_slices.jsonl",
            "reporter_runs": "reporter_runs.jsonl",
            "packets": "packets.jsonl",
            "control_events": "control_events.jsonl",
            "controller_results": "controller_results.jsonl",
            "controller_transcript": "controller_transcript.jsonl",
        },
    }
    if solve_environment_preflight is not None:
        manifest["artifacts"]["solve_environment_preflight"] = (
            "solve_environment_preflight.json"
        )
    if solve_runtime_setup is not None:
        manifest["artifacts"]["solve_runtime_setup"] = "solve_runtime_setup.json"
    manifest["evaluation_state"] = "awaiting_evaluation"
    manifest["artifacts"]["solve_manifest"] = "solve_manifest.json"
    _write_json(out_dir / "solve_manifest.json", manifest)
    _write_json(out_dir / "run_manifest.json", manifest)
    return manifest


def _load_json(path: Path | None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path is not None else None


def _snapshot_solve_final_workspace(workspace: Path, out_dir: Path) -> Path:
    """Persist the exact solve result before any evaluator-side work begins."""

    snapshot = out_dir / "solve_final_workspace"
    temporary = out_dir / ".solve_final_workspace.tmp"
    if snapshot.exists() or snapshot.is_symlink() or temporary.exists():
        raise ValueError("solve-final workspace snapshot artifact already exists")
    try:
        shutil.copytree(workspace, temporary, symlinks=True)
        temporary.replace(snapshot)
    except Exception:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        raise
    return snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("controlled", "no-control"), required=True)
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--worker-model", default=DEFAULT_MODEL)
    parser.add_argument("--controller-model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--controller-provider",
        choices=("model", "non-adaptive-fixed"),
        default="model",
        help="Use the tested model or the deterministic fixed-goal ablation.",
    )
    parser.add_argument("--base-url", default=DEFAULT_GATEWAY)
    parser.add_argument("--credential-profile-id", default="default-gateway")
    parser.add_argument("--controller-base-url")
    parser.add_argument("--controller-credential-profile-id")
    parser.add_argument(
        "--controller-api-key-env",
        default=_llm.DEFAULT_API_KEY_ENV,
        help=("Environment variable containing the Controller API credential."),
    )
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--mount-point", default="/work")
    parser.add_argument(
        "--cpus",
        type=float,
        help="Source task CPU limit; omitted when the source declares no limit.",
    )
    parser.add_argument(
        "--memory-mb",
        type=int,
        help="Source task memory limit in MiB; omitted when undeclared.",
    )
    parser.add_argument(
        "--network",
        choices=("none", "bridge", "host"),
        default="bridge",
        help=(
            "Solve-container network policy. Dataset adapters should pass the "
            "source task's declared value explicitly."
        ),
    )
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help=(
            "Ephemeral host directory mounted outside the submitted workspace "
            "for source-runtime restoration such as a checkpoint venv."
        ),
    )
    parser.add_argument(
        "--runtime-venv-relative-path",
        help=(
            "Relative venv path below --runtime-dir; its bin directory is "
            "placed first on PATH for worker and reporter tools."
        ),
    )
    parser.add_argument(
        "--solve-setup-command",
        action="append",
        default=[],
        help=(
            "Public source-benchmark resume command executed before model "
            "inference. Repeat to preserve command order."
        ),
    )
    parser.add_argument(
        "--solve-setup-timeout-sec",
        type=int,
        default=900,
        help="Per-command timeout for source-runtime restoration.",
    )
    parser.add_argument("--worker-wall-time-sec", type=int, default=7200)
    parser.add_argument("--reporter-wall-time-sec", type=int, default=900)
    parser.add_argument("--gateway-timeout-sec", type=int, default=300)
    parser.add_argument("--tool-timeout-sec", type=int, default=120)
    parser.add_argument("--serialized-messages", type=Path)
    parser.add_argument(
        "--evaluator-plan",
        type=Path,
        help=(
            "Private official-adapter plan evaluated only after model access ends. "
            "SCBench evaluates the sealed workspace after solve teardown; BeyondSWE "
            "runs the private verifier in the still-live source solve container."
        ),
    )
    parser.add_argument(
        "--resume-run",
        action="store_true",
        help="Resume the first inner-loop slice from a safe checkpoint.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.controller_provider == "non-adaptive-fixed":
        if args.arm != "controlled":
            parser.error("non-adaptive fixed control is controlled-only")
    task = require_task_text(args.task_file.read_text(encoding="utf-8"))
    workspace = args.workspace.resolve()
    out_dir = args.out_dir.resolve()
    runtime_dir = (
        args.runtime_dir.expanduser().resolve()
        if args.runtime_dir is not None
        else None
    )
    if args.runtime_venv_relative_path and runtime_dir is None:
        parser.error("--runtime-venv-relative-path requires --runtime-dir")
    if args.solve_setup_timeout_sec <= 0:
        parser.error("--solve-setup-timeout-sec must be positive")
    if args.cpus is not None and args.cpus <= 0:
        parser.error("--cpus must be positive")
    if args.memory_mb is not None and args.memory_mb <= 0:
        parser.error("--memory-mb must be positive")
    if runtime_dir is not None:
        if any(
            runtime_dir == path
            or runtime_dir in path.parents
            or path in runtime_dir.parents
            for path in (workspace, out_dir)
        ):
            parser.error("--runtime-dir must be disjoint from workspace and out-dir")
    evaluator_plan = None
    if args.evaluator_plan is not None:
        plan_path = args.evaluator_plan.resolve()
        if plan_path == workspace or workspace in plan_path.parents:
            parser.error("--evaluator-plan must be outside the solve workspace")
        evaluator_plan = _load_json(plan_path)
        plan_errors = validate_evaluator_plan(evaluator_plan)
        if plan_errors:
            parser.error("invalid evaluator plan: " + ",".join(plan_errors))
        private_config = evaluator_plan.get("adapter_config") or {}
        if evaluator_plan.get("adapter_kind") == "scbench":
            private_path_fields = (
                "runner_root",
                "problems_root",
                "env_config",
                "private_bundle_path",
            )
        else:
            private_path_fields = ("task_dir",)
        private_paths = [
            Path(str(private_config[field])).resolve()
            for field in private_path_fields
            if private_config.get(field)
        ]
        for private_path in private_paths:
            if private_path == workspace or workspace in private_path.parents:
                parser.error(
                    "evaluator source, configuration, and private bundle paths "
                    "must be outside the solve workspace"
                )
    recovery_checkpoint = None
    if args.resume_run:
        if args.serialized_messages is not None:
            parser.error("--resume-run reads only the durable recovery checkpoint")
        checkpoint_path = out_dir / "recovery_checkpoint.json"
        if not checkpoint_path.is_file():
            parser.error("--resume-run requires recovery_checkpoint.json in --out-dir")
        try:
            recovery_checkpoint = read_checkpoint(checkpoint_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"invalid recovery checkpoint: {exc}")
    worker = _llm.WorkerClient(
        model=args.worker_model,
        base_url=args.base_url,
        timeout_sec=args.gateway_timeout_sec,
        transport="http",
        streaming=True,
        credential_profile_id=args.credential_profile_id,
    )
    harness_identity = capture_harness_identity(ROOT)
    controller = None
    if args.arm == "controlled":
        if args.controller_provider == "non-adaptive-fixed":
            controller = NonAdaptiveFixedController()
        else:
            controller_profile = (
                args.controller_credential_profile_id or args.credential_profile_id
            )
            controller = ControllerChatClient(
                model=args.controller_model,
                base_url=args.controller_base_url or args.base_url,
                temperature=0.0,
                timeout=args.gateway_timeout_sec,
                credential_profile_id=controller_profile,
                api_key_env=args.controller_api_key_env,
                transport="http",
                # WorkerClient owns the same-call credential retry policy.
                max_retries=1,
                seed=args.seed,
            )
    elif args.controller_provider != "model":
        parser.error("non-model controller options are controlled-only")
    main_sandbox = _sandbox.Sandbox(
        workdir=workspace,
        image=args.image,
        network=args.network,
        mount_point=args.mount_point,
        exec_timeout=args.tool_timeout_sec,
        cpus=args.cpus,
        memory_mb=args.memory_mb,
        runtime_dir=runtime_dir,
        runtime_venv_relative_path=args.runtime_venv_relative_path,
    )

    def reporter_factory() -> Any:
        return _sandbox.Sandbox(
            workdir=workspace,
            image=args.image,
            network=args.network,
            mount_point=args.mount_point,
            exec_timeout=args.tool_timeout_sec,
            cpus=args.cpus,
            memory_mb=args.memory_mb,
            workspace_read_only=True,
            runtime_dir=runtime_dir,
            runtime_read_only=True,
            runtime_venv_relative_path=args.runtime_venv_relative_path,
        )

    def execute_attempt() -> dict[str, Any]:
        live_evaluation_receipt: dict[str, Any] | None = None
        with main_sandbox:
            solve_runtime_setup = (
                _run_solve_runtime_setup(
                    main_sandbox,
                    list(args.solve_setup_command),
                    args.solve_setup_timeout_sec,
                )
                if args.solve_setup_command
                else None
            )
            if (
                solve_runtime_setup is not None
                and solve_runtime_setup["status"] != "passed"
            ):
                _write_json(
                    out_dir / "solve_runtime_setup.json",
                    solve_runtime_setup,
                )
                raise RuntimeError("solve runtime setup failed")
            if recovery_checkpoint is not None:
                solve_preflight = recovery_checkpoint.get("solve_environment_preflight")
                if not isinstance(solve_preflight, dict):
                    raise ValueError(
                        "recovery checkpoint is missing the original solve preflight"
                    )
            else:
                solve_preflight = build_solve_environment_preflight(
                    workspace=workspace,
                    sandbox=main_sandbox,
                )
            if solve_preflight["status"] != "passed":
                out_dir.mkdir(parents=True, exist_ok=True)
                _write_json(
                    out_dir / "solve_environment_preflight.json", solve_preflight
                )
                raise RuntimeError("solve environment production preflight failed")
            result_manifest = run_episode(
                arm=args.arm,
                task=task,
                workspace=workspace,
                out_dir=out_dir,
                worker=worker,
                main_sandbox=main_sandbox,
                reporter_sandbox_factory=(
                    reporter_factory
                    if args.arm == "controlled"
                    and args.controller_provider != "non-adaptive-fixed"
                    else None
                ),
                controller=controller,
                sample_id=args.sample_id,
                seed=args.seed,
                worker_wall_time_sec=args.worker_wall_time_sec,
                reporter_wall_time_sec=args.reporter_wall_time_sec,
                serialized_messages=_load_json(args.serialized_messages),
                solve_environment_preflight=solve_preflight,
                solve_runtime_setup=solve_runtime_setup,
                recovery_checkpoint=recovery_checkpoint,
                harness_identity=harness_identity,
            )
            if (
                isinstance(evaluator_plan, dict)
                and evaluator_plan.get("adapter_kind") == "beyondswe_harbor"
            ):
                # This is the last model-facing boundary. Seal the candidate
                # before Harbor's private tests can modify the bind-mounted
                # workspace, then run the shared verifier in this same
                # environment while it is still alive.
                solve_snapshot = _snapshot_solve_final_workspace(
                    workspace,
                    out_dir,
                )
                result_manifest["artifacts"]["solve_final_workspace"] = (
                    "solve_final_workspace"
                )
                _write_json(out_dir / "solve_manifest.json", result_manifest)
                _write_json(out_dir / "run_manifest.json", result_manifest)
                from looparena.evaluators import evaluate_with_plan

                live_evaluation_receipt = evaluate_with_plan(
                    workspace=solve_snapshot,
                    output_dir=out_dir / "evaluation",
                    plan=evaluator_plan,
                    solve_sandbox=main_sandbox,
                )
        if live_evaluation_receipt is not None:
            from looparena.commands.evaluate import attach_evaluation_receipt

            result_manifest = attach_evaluation_receipt(
                run_dir=out_dir,
                plan=evaluator_plan,
                receipt=live_evaluation_receipt,
            )
        else:
            _snapshot_solve_final_workspace(workspace, out_dir)
            result_manifest["artifacts"]["solve_final_workspace"] = (
                "solve_final_workspace"
            )
            _write_json(out_dir / "solve_manifest.json", result_manifest)
            _write_json(out_dir / "run_manifest.json", result_manifest)
            if args.evaluator_plan is not None:
                from looparena.commands.evaluate import evaluate_run

                result_manifest = evaluate_run(
                    run_dir=out_dir,
                    plan=evaluator_plan,
                )
        return result_manifest

    attempt_path = out_dir / "attempt_state.json"
    previous_attempt = _load_json(attempt_path) if attempt_path.is_file() else {}
    attempt_index = int((previous_attempt or {}).get("attempt_index") or 0) + 1
    attempt = {
        "attempt_index": attempt_index,
        "status": "running",
        "pid": os.getpid(),
        "started_unix_sec": time.time(),
        "arm": args.arm,
        "sample_id": args.sample_id,
        "seed": args.seed,
        "resume_run": bool(args.resume_run),
        "harness_identity": harness_identity,
    }
    atomic_write_json(attempt_path, attempt)

    previous_handlers: dict[int, Any] = {}

    def interrupt(signum: int, frame: Any) -> None:
        raise _RunSignalInterruption(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)
    try:
        manifest = execute_attempt()
    except BaseException as exc:
        signum = exc.signum if isinstance(exc, _RunSignalInterruption) else None
        interrupted = {
            **attempt,
            "status": "interrupted",
            "finished_unix_sec": time.time(),
            "signal": signum,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc)[:500],
            "stderr_capture": "external_launcher_required",
        }
        atomic_write_json(attempt_path, interrupted)
        _append_jsonl_durable(out_dir / "interruption_receipts.jsonl", interrupted)
        raise
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)

    atomic_write_json(
        attempt_path,
        {
            **attempt,
            "status": "completed",
            "finished_unix_sec": time.time(),
        },
    )
    manifest["artifacts"]["attempt_state"] = "attempt_state.json"
    if (out_dir / "recovery_checkpoint.json").is_file():
        manifest["artifacts"]["recovery_checkpoint"] = "recovery_checkpoint.json"
    if (out_dir / "interruption_receipts.jsonl").is_file():
        manifest["artifacts"]["interruption_receipts"] = "interruption_receipts.jsonl"
    _write_json(out_dir / "run_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
