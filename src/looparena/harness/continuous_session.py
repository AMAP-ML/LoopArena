"""Natural-inner-loop controlled and uninterrupted no-control harness sessions."""

from __future__ import annotations

import copy
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, ContextManager, Protocol

from looparena.runtime.llm import _redact

from . import controller as controller_api
from . import rendering as R
from .packet_compiler import (
    compile_packet_from_reporter,
    validate_report_evidence_refs,
)
from .transcript import (
    derive_worker_evidence_turns,
    worker_evidence_ref,
)

MAIN_WORKER_TURN_LIMIT = 600
REPORTER_TURN_LIMIT = 50
CONTROL_CYCLE_LIMIT = 128
CONTROL_CHANNEL_WALL_TIME_LIMIT_SEC = 86_400


class Conversation(Protocol):
    messages: list[dict[str, Any]]
    total_repo_actions: int
    total_model_calls: int

    def initialize(self, common_start_user_turn: str) -> None: ...
    def append_user_turn(self, content: str) -> None: ...
    def run_main_until_boundary(self, **kwargs: Any) -> dict[str, Any]: ...
    def run_forked_reporter(self, **kwargs: Any) -> dict[str, Any]: ...


MainProgressSink = Callable[[int, dict[str, Any]], None]
ReporterSandboxFactory = Callable[[], Any]


@dataclass
class ContinuousSessionResult:
    arm: str
    status: str
    termination_reason: str
    terminal_action: str
    packets: list[dict[str, Any]] = field(default_factory=list)
    reporter_runs: list[dict[str, Any]] = field(default_factory=list)
    main_worker_slices: list[dict[str, Any]] = field(default_factory=list)
    control_events: list[dict[str, Any]] = field(default_factory=list)
    controller_results: list[controller_api.ControllerResult] = field(
        default_factory=list
    )
    controller_conversation: list[controller_api.Message] = field(default_factory=list)
    main_worker_turns: int = 0
    main_worker_repo_tool_calls: int = 0
    reporter_turns: int = 0
    controller_calls: int = 0
    main_worker_wall_time_sec: float = 0.0
    control_channel_wall_time_sec: float = 0.0


def validate_reporter_shape(report: dict[str, Any]) -> list[str]:
    if not isinstance(report, dict):
        return ["round_report_must_be_object"]
    required = {
        "task_context_and_constraints",
        "work_history_and_current_state",
        "verification_and_evidence",
        "open_issues_and_uncertainty",
    }
    errors = [f"round_report_missing:{key}" for key in sorted(required - set(report))]
    for field_name in sorted(required):
        if (
            not isinstance(report.get(field_name), str)
            or not report[field_name].strip()
        ):
            errors.append(f"round_report_{field_name}_must_be_nonempty_string")
    return errors


_REPORTER_INFRASTRUCTURE_ERRORS = {
    "reporter_context_capacity_exhausted",
    "reporter_gateway_timeout",
    "reporter_provider_failure",
    "reporter_sandbox_failure",
}
_REPORTER_BUDGET_ERRORS = {
    "reporter_turn_budget_exhausted_without_round_report",
    "reporter_wall_time_exhausted",
}


def _classify_reporter_failure(
    reporter_run: dict[str, Any],
) -> tuple[str, str]:
    """Separate external infrastructure from countable reporter-system failure."""

    errors = [
        str(error)
        for error in reporter_run.get("errors") or []
        if isinstance(error, str) and error
    ]
    for error in errors:
        if error in _REPORTER_INFRASTRUCTURE_ERRORS:
            return "reporter_infrastructure_failure", error
    for error in errors:
        if error in _REPORTER_BUDGET_ERRORS:
            return "reporter_budget_exhausted", error
    return "reporter_protocol_failure", (
        errors[0] if errors else "reporter_missing_round_report"
    )


class ContinuousHarnessSession:
    """Run exactly one proposal-conformant no-control or controlled episode."""

    def __init__(
        self,
        *,
        arm: str,
        start_context: dict[str, Any],
        initial_control_packet: dict[str, Any] | None = None,
        initial_main_events: list[dict[str, Any]] | None = None,
        conversation: Conversation,
        main_progress_sink: MainProgressSink | None = None,
        controller: controller_api.ChatClient | None = None,
        reporter_sandbox_factory: ReporterSandboxFactory | None = None,
        max_main_worker_turns: int = MAIN_WORKER_TURN_LIMIT,
        max_reporter_turns: int = REPORTER_TURN_LIMIT,
        max_control_cycles: int = CONTROL_CYCLE_LIMIT,
        max_control_channel_wall_time_sec: int
        | float = CONTROL_CHANNEL_WALL_TIME_LIMIT_SEC,
        seed: int = 0,
        wall_time_limit_sec: int | float | None = None,
        reporter_wall_time_limit_sec: int | float | None = None,
        resume_main_slice_prefix: dict[str, Any] | None = None,
        resume_control_events: list[dict[str, Any]] | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        if arm not in {"no-control", "controlled"}:
            raise ValueError("arm must be no-control or controlled")
        if arm == "controlled" and controller is None:
            raise ValueError("controlled arm requires a controller provider")
        if arm == "no-control" and controller is not None:
            raise ValueError("no-control arm must not receive a controller")
        if arm == "no-control" and initial_control_packet is not None:
            raise ValueError("no-control must not receive a controller packet")
        fixed_control = (
            arm == "controlled"
            and getattr(controller, "provider_kind", "") == "non-adaptive-fixed"
        )
        if (
            arm == "controlled"
            and not fixed_control
            and reporter_sandbox_factory is None
        ):
            raise ValueError(
                "controlled arm requires a read-only reporter sandbox factory"
            )
        if max_main_worker_turns != MAIN_WORKER_TURN_LIMIT:
            raise ValueError("Core main-worker budget must be 600 ReAct turns")
        if max_reporter_turns != REPORTER_TURN_LIMIT:
            raise ValueError("Core reporter budget must be 50 ReAct turns per call")
        if max_control_cycles <= 0:
            raise ValueError("max_control_cycles must be positive")
        if (
            isinstance(max_control_channel_wall_time_sec, bool)
            or not isinstance(max_control_channel_wall_time_sec, (int, float))
            or float(max_control_channel_wall_time_sec) <= 0
        ):
            raise ValueError("max_control_channel_wall_time_sec must be positive")
        self.arm = arm
        self.fixed_control = fixed_control
        self.current_packet = copy.deepcopy(initial_control_packet or start_context)
        self.initial_control_packet = copy.deepcopy(initial_control_packet)
        self.conversation = conversation
        self.main_progress_sink = main_progress_sink
        self.controller = controller
        self.reporter_sandbox_factory = reporter_sandbox_factory
        self.max_main_worker_turns = max_main_worker_turns
        self.max_reporter_turns = max_reporter_turns
        self.max_control_cycles = max_control_cycles
        self.max_control_channel_wall_time_sec = float(
            max_control_channel_wall_time_sec
        )
        self.seed = int(seed)
        self.wall_time_limit_sec = wall_time_limit_sec
        self.reporter_wall_time_limit_sec = reporter_wall_time_limit_sec
        self.packets: list[dict[str, Any]] = []
        self.reporter_runs: list[dict[str, Any]] = []
        self.main_worker_slices: list[dict[str, Any]] = []
        self.control_events: list[dict[str, Any]] = copy.deepcopy(
            resume_control_events or []
        )
        self.controller_results: list[controller_api.ControllerResult] = []
        self._controller_conversation: list[controller_api.Message] = []
        self._initialized = False
        self._resume_main_slice_prefix = copy.deepcopy(resume_main_slice_prefix or {})
        self._resume_state = copy.deepcopy(resume_state or {})
        self._next_action = "call_worker"
        self._pending_reporter_state: dict[str, Any] | None = None
        self._pending_controller_state: dict[str, Any] | None = None
        self.status = "not_started"
        self.termination_reason = ""
        self.terminal_action = ""
        self._main_events_since_control = copy.deepcopy(initial_main_events or [])
        initial_event_refs = [
            str(event.get("event_id") or "")
            for event in self._main_events_since_control
            if isinstance(event, dict)
        ]
        if any(not ref for ref in initial_event_refs) or len(initial_event_refs) != len(
            set(initial_event_refs)
        ):
            raise ValueError(
                "initial_main_events require unique non-empty event_id values"
            )
        self._main_worker_wall_time_used_sec = 0.0
        if self._resume_main_slice_prefix:
            self._main_worker_wall_time_used_sec = float(
                self._resume_main_slice_prefix.get("wall_time_sec") or 0.0
            )
        self._control_channel_wall_time_used_sec = 0.0
        controlled_bootstrap = arm == "controlled" and initial_control_packet is None
        self._current_worker_action = "verify" if controlled_bootstrap else ""
        self._active_contract = (
            R.build_controlled_bootstrap_contract() if controlled_bootstrap else None
        )
        if self._resume_state:
            if arm != "controlled":
                raise ValueError("controlled session recovery is controlled-only")
            if not initial_control_packet:
                raise ValueError(
                    "controlled session recovery requires a current packet"
                )
            self.packets = copy.deepcopy(self._resume_state.get("packets") or [])
            self.reporter_runs = copy.deepcopy(
                self._resume_state.get("reporter_runs") or []
            )
            self.main_worker_slices = copy.deepcopy(
                self._resume_state.get("main_worker_slices") or []
            )
            self.controller_results = [
                self._controller_result_from_row(row)
                for row in self._resume_state.get("controller_results") or []
            ]
            saved_controller_conversation = self._resume_state.get(
                "controller_conversation"
            )
            if isinstance(saved_controller_conversation, list):
                self._controller_conversation = copy.deepcopy(
                    saved_controller_conversation
                )
            self._main_worker_wall_time_used_sec = float(
                self._resume_state.get("main_worker_wall_time_sec") or 0.0
            )
            self._control_channel_wall_time_used_sec = float(
                self._resume_state.get("control_channel_wall_time_sec") or 0.0
            )
            self._main_events_since_control = copy.deepcopy(
                self._resume_state.get("main_events_since_control") or []
            )
            self._current_worker_action = str(
                self._resume_state.get("current_worker_action") or ""
            )
            self._active_contract = copy.deepcopy(
                self._resume_state.get("active_contract")
            )
            saved_next_action = str(self._resume_state.get("next_action") or "")
            if saved_next_action:
                if saved_next_action not in {
                    "call_worker",
                    "call_reporter",
                    "call_controller",
                    "apply_contract",
                }:
                    raise ValueError("recovery state has an unsupported next action")
                self._next_action = saved_next_action
            self._pending_reporter_state = copy.deepcopy(
                self._resume_state.get("pending_reporter_state")
            )
            self._pending_controller_state = copy.deepcopy(
                self._resume_state.get("pending_controller_state")
            )
        if self.controller is not None and hasattr(self.controller, "call_index"):
            # Controller seeds are indexed over the logical run, not the number
            # of operating-system attempts used to finish it.
            setattr(self.controller, "call_index", len(self.controller_results))

    @staticmethod
    def _controller_result_row(
        result: controller_api.ControllerResult,
    ) -> dict[str, Any]:
        return {
            "contract": copy.deepcopy(result.contract),
            "validation_errors": list(result.validation_errors),
            "failure_kind": result.failure_kind,
            "failure_diagnostics": copy.deepcopy(result.failure_diagnostics),
            "model_call_audits": copy.deepcopy(result.model_call_audits),
            "context_compaction_audit": copy.deepcopy(result.context_compaction_audit),
        }

    @staticmethod
    def _controller_result_from_row(
        row: dict[str, Any],
        *,
        messages: list[controller_api.Message] | None = None,
        raw_response: str = "",
    ) -> controller_api.ControllerResult:
        return controller_api.ControllerResult(
            contract=copy.deepcopy(row.get("contract") or {}),
            messages=copy.deepcopy(messages or []),
            raw_response=raw_response,
            validation_errors=list(row.get("validation_errors") or []),
            failure_kind=str(row.get("failure_kind") or ""),
            failure_diagnostics=copy.deepcopy(row.get("failure_diagnostics") or {}),
            model_call_audits=copy.deepcopy(row.get("model_call_audits") or []),
            context_compaction_audit=copy.deepcopy(
                row.get("context_compaction_audit") or {}
            ),
        )

    def _session_recovery_state(
        self,
        cycle_index: int,
        next_action: str,
        *,
        pending_reporter_state: dict[str, Any] | None = None,
        pending_controller_state: dict[str, Any] | None = None,
        main_worker_turns: int | None = None,
        repo_tool_calls: int | None = None,
        main_worker_wall_time_sec: float | None = None,
        control_channel_wall_time_sec: float | None = None,
    ) -> dict[str, Any]:
        return {
            "cycle_index": cycle_index,
            "next_action": next_action,
            "current_packet": copy.deepcopy(self.current_packet),
            "packets": copy.deepcopy(self.packets),
            "reporter_runs": copy.deepcopy(self.reporter_runs),
            "main_worker_slices": copy.deepcopy(self.main_worker_slices),
            "controller_results": [
                self._controller_result_row(result)
                for result in self.controller_results
            ],
            "controller_conversation": copy.deepcopy(self._controller_conversation),
            "main_worker_turns": (
                self.conversation.total_model_calls
                if main_worker_turns is None
                else main_worker_turns
            ),
            "repo_tool_calls": (
                self.conversation.total_repo_actions
                if repo_tool_calls is None
                else repo_tool_calls
            ),
            "main_worker_wall_time_sec": round(
                self._main_worker_wall_time_used_sec
                if main_worker_wall_time_sec is None
                else main_worker_wall_time_sec,
                6,
            ),
            "control_channel_wall_time_sec": round(
                self._control_channel_wall_time_used_sec
                if control_channel_wall_time_sec is None
                else control_channel_wall_time_sec,
                6,
            ),
            "main_events_since_control": copy.deepcopy(self._main_events_since_control),
            "current_worker_action": self._current_worker_action,
            "active_contract": copy.deepcopy(self._active_contract),
            "pending_reporter_state": copy.deepcopy(pending_reporter_state),
            "pending_controller_state": copy.deepcopy(pending_controller_state),
        }

    def _save_recovery_boundary(
        self,
        cycle_index: int,
        next_action: str,
        *,
        pending_reporter_state: dict[str, Any] | None = None,
        pending_controller_state: dict[str, Any] | None = None,
        control_channel_wall_time_sec: float | None = None,
    ) -> None:
        if self.main_progress_sink is None:
            return
        self.main_progress_sink(
            cycle_index,
            {
                "phase": next_action,
                "messages": copy.deepcopy(self.conversation.messages),
                "main_worker_turns": self.conversation.total_model_calls,
                "repo_tool_calls": self.conversation.total_repo_actions,
                "current_slice_main_worker_turns": 0,
                "current_slice_repo_tool_calls": 0,
                "control_decision": self._current_worker_action,
                "control_events": copy.deepcopy(self.control_events),
                "controlled_session_state": self._session_recovery_state(
                    cycle_index,
                    next_action,
                    pending_reporter_state=pending_reporter_state,
                    pending_controller_state=pending_controller_state,
                    control_channel_wall_time_sec=control_channel_wall_time_sec,
                ),
                "tool_events": [],
                "model_response_audit": [],
                "consecutive_empty_responses": 0,
                "protocol_errors": [],
                "wall_time_sec": self._main_worker_wall_time_used_sec,
            },
        )

    def _control_channel_capacity_available(self) -> bool:
        if len(self.controller_results) >= self.max_control_cycles:
            self.status = "control_channel_budget_exhausted"
            self.termination_reason = "control_cycle_limit_exhausted"
            return False
        if (
            self._control_channel_wall_time_used_sec
            >= self.max_control_channel_wall_time_sec
        ):
            self.status = "control_channel_budget_exhausted"
            self.termination_reason = "control_channel_wall_time_exhausted"
            return False
        return True

    def _record_control_channel_time(
        self, started: float, *, excluded_sec: float = 0.0
    ) -> bool:
        elapsed = time.monotonic() - started
        self._control_channel_wall_time_used_sec += max(0.0, elapsed - excluded_sec)
        if (
            self._control_channel_wall_time_used_sec
            < self.max_control_channel_wall_time_sec
        ):
            return True
        if self.status not in {"running", "completed"}:
            return True
        self.status = "control_channel_budget_exhausted"
        self.termination_reason = "control_channel_wall_time_exhausted"
        return False

    def _apply_contract(
        self,
        packet: dict[str, Any],
        cycle_index: int,
        *,
        packet_generated: bool,
        initial_replay_control: bool = False,
        pending_controller_state: dict[str, Any] | None = None,
    ) -> bool:
        assert self.controller is not None
        if pending_controller_state is not None:
            result_row = pending_controller_state.get("result")
            if not isinstance(result_row, dict):
                raise ValueError("pending controller state has no result")
            messages = pending_controller_state.get("messages")
            if not isinstance(messages, list):
                raise ValueError("pending controller state has no messages")
            raw_response = pending_controller_state.get("raw_response")
            if not isinstance(raw_response, str):
                raise ValueError("pending controller state has no response")
            result = self._controller_result_from_row(
                result_row,
                messages=messages,
                raw_response=raw_response,
            )
            controller_processing_wall_time = float(
                pending_controller_state.get("controller_wall_time_sec") or 0.0
            )
            controller_wall_time = float(
                pending_controller_state.get("controller_elapsed_wall_time_sec") or 0.0
            )
            packet_generated = bool(
                pending_controller_state.get("packet_generated", packet_generated)
            )
            initial_replay_control = bool(
                pending_controller_state.get(
                    "initial_replay_control", initial_replay_control
                )
            )
        else:
            self._save_recovery_boundary(cycle_index, "call_controller")
            controller_api.bind_controller_packet(self.controller, packet)
            controller_api.bind_controller_cycle(self.controller, cycle_index)
            previous_worker_slice = (
                self.main_worker_slices[-1] if self.main_worker_slices else {}
            )
            previous_repo_tool_calls = int(
                previous_worker_slice.get("repo_tool_calls") or 0
            )
            controller_api.bind_controller_worker_activity(
                self.controller,
                previous_repo_tool_calls,
                list(previous_worker_slice.get("tool_events") or []),
            )
            controller_started = time.monotonic()
            result = controller_api.run_controller(
                packet,
                self.controller,
                previous_messages=self._controller_conversation,
            )
            controller_wall_time = time.monotonic() - controller_started
            controller_processing_wall_time = controller_wall_time
            self.controller_results.append(result)
            self._record_control_channel_time(controller_started)
        event: dict[str, Any] = {
            "cycle_index": cycle_index,
            "controller_result_index": len(self.controller_results) - 1,
            "controller_called": True,
            "packet_generated": packet_generated,
            "initial_replay_control": initial_replay_control,
            "controller_wall_time_sec": round(controller_processing_wall_time, 6),
            "controller_elapsed_wall_time_sec": round(controller_wall_time, 6),
        }
        if result.validation_errors:
            self._controller_conversation = [
                *copy.deepcopy(result.messages),
                {"role": "assistant", "content": result.raw_response},
            ]
            event["validation_errors"] = list(result.validation_errors)
            event["action"] = ""
            self.control_events.append(event)
            if result.failure_kind == "infrastructure_transport":
                self.status = "controller_provider_failure"
                self.termination_reason = "controller_provider_failure"
            elif result.failure_kind == "controller_context_preflight_exhausted":
                self.status = "controller_context_preflight_exhausted"
                self.termination_reason = "controller_context_preflight_exhausted"
            else:
                self.status = "invalid_contract"
                self.termination_reason = "invalid_contract"
            return False
        if pending_controller_state is None:
            pending_controller_state = {
                "result": self._controller_result_row(result),
                "messages": copy.deepcopy(result.messages),
                "raw_response": result.raw_response,
                "packet_generated": packet_generated,
                "initial_replay_control": initial_replay_control,
                "controller_wall_time_sec": round(controller_processing_wall_time, 6),
                "controller_elapsed_wall_time_sec": round(controller_wall_time, 6),
            }
            self._save_recovery_boundary(
                cycle_index,
                "apply_contract",
                pending_controller_state=pending_controller_state,
            )
        self._controller_conversation = [
            *copy.deepcopy(result.messages),
            {"role": "assistant", "content": result.raw_response},
        ]
        contract = result.contract
        self._active_contract = copy.deepcopy(contract)
        decision = contract.get("control_decision") or {}
        action = str(decision.get("action") or "")
        self._current_worker_action = action
        event["action"] = action
        self.terminal_action = action
        if action == "stop":
            self.control_events.append(event)
            self.status = "completed"
            self.termination_reason = "controller_stop"
            return False
        runtime_budget = None
        if self.fixed_control:
            # The fixed policy never reads Reporter output, and Reporter output is
            # never shown to the Worker. Bypassing that redundant call preserves
            # the fixed-control treatment, so existing fixed-control experiments
            # do not need to be rerun. Supply the live count directly so the
            # Worker-visible budget also remains unchanged.
            runtime_budget = R.resolve_worker_budget(packet)
            runtime_budget["remaining_inner_react_turns"] = max(
                0,
                self.max_main_worker_turns - self.conversation.total_model_calls,
            )
        continuation = R.render_controlled_continuation(
            contract,
            packet,
            runtime_budget=runtime_budget,
        )
        self.conversation.append_user_turn(continuation)
        event["continuation_message_index"] = len(self.conversation.messages) - 1
        self.control_events.append(event)
        self._save_recovery_boundary(cycle_index, "call_worker")
        return True

    def _open_reporter_sandbox(self) -> ContextManager[Any]:
        assert self.reporter_sandbox_factory is not None
        sandbox = self.reporter_sandbox_factory()
        if hasattr(sandbox, "__enter__") and hasattr(sandbox, "__exit__"):
            return sandbox
        return nullcontext(sandbox)

    def _report_and_compile(
        self,
        cycle_index: int,
        *,
        reporter_resume_state: dict[str, Any] | None = None,
    ) -> bool:
        worker_evidence_turns = derive_worker_evidence_turns(
            self.conversation.messages,
        )
        allowed_evidence_refs = {
            worker_evidence_ref(event)
            for event in worker_evidence_turns
            if worker_evidence_ref(event)
        }
        prompt = R.render_reporter_prompt(
            str(self.current_packet.get("task") or ""),
            self.conversation.messages,
        )
        self._save_recovery_boundary(
            cycle_index,
            "call_reporter",
            pending_reporter_state=reporter_resume_state,
        )
        control_started = time.monotonic()
        reporter_started = time.monotonic()
        initial_reporter_wall_time = float(
            (reporter_resume_state or {}).get("wall_time_sec") or 0.0
        )

        def save_reporter_progress(state: dict[str, Any]) -> None:
            current_reporter_wall_time = float(state.get("wall_time_sec") or 0.0)
            projected_control_time = self._control_channel_wall_time_used_sec + max(
                0.0,
                current_reporter_wall_time - initial_reporter_wall_time,
            )
            self._save_recovery_boundary(
                cycle_index,
                "call_reporter",
                pending_reporter_state=state,
                control_channel_wall_time_sec=projected_control_time,
            )

        try:
            with self._open_reporter_sandbox() as reporter_sandbox:
                reporter_run = self.conversation.run_forked_reporter(
                    reporter_sandbox=reporter_sandbox,
                    reporter_system_prompt=R.REPORTER_SYSTEM_PROMPT,
                    reporter_prompt=prompt,
                    max_reporter_turns=self.max_reporter_turns,
                    seed=self.seed + 100_000 + cycle_index * self.max_reporter_turns,
                    wall_time_limit_sec=self.reporter_wall_time_limit_sec,
                    report_validator=lambda report: [
                        *validate_reporter_shape(report),
                    ],
                    report_event_validator=lambda report, _events: (
                        validate_report_evidence_refs(
                            report,
                            allowed_evidence_refs,
                        )
                    ),
                    initial_state=reporter_resume_state,
                    progress_sink=save_reporter_progress,
                )
        except (
            Exception
        ) as exc:  # Reporter container/runtime never becomes a model score.
            reporter_run = {
                "status": "failed",
                "report": None,
                "reporter_turns": 0,
                "tool_events": [],
                "model_response_audit": [],
                "errors": ["reporter_sandbox_failure"],
                "provider_failure": {
                    "error_type": type(exc).__name__,
                    "redacted_error": _redact(str(exc))[:500],
                },
            }
        reporter_run.setdefault(
            "wall_time_sec", round(time.monotonic() - reporter_started, 6)
        )
        self.reporter_runs.append(reporter_run)
        if reporter_run.get("status") != "completed":
            self.status, self.termination_reason = _classify_reporter_failure(
                reporter_run
            )
            self._record_control_channel_time(control_started)
            return False
        report = reporter_run.get("report")
        if not isinstance(report, dict):
            self.status = "reporter_protocol_failure"
            self.termination_reason = "reporter_missing_round_report"
            self._record_control_channel_time(control_started)
            return False
        try:
            next_packet = compile_packet_from_reporter(
                self.current_packet,
                reporter_report=report,
                worker_evidence_turns=worker_evidence_turns,
                main_worker_turns_used=self.conversation.total_model_calls,
                cycle_index=cycle_index,
                active_contract=self._active_contract,
            )
        except (TypeError, ValueError):
            self.status = "packet_compiler_failed"
            self.termination_reason = "packet_compiler_rejected_report"
            self._record_control_channel_time(control_started)
            return False
        self.current_packet = next_packet
        self.packets.append(copy.deepcopy(next_packet))
        self._main_events_since_control = []
        within_control_wall_time = self._record_control_channel_time(control_started)
        if not within_control_wall_time:
            return False
        self._save_recovery_boundary(cycle_index, "call_controller")
        return True

    def run(
        self,
        *,
        resume_existing_conversation: bool = False,
        resume_in_progress: bool = False,
    ) -> ContinuousSessionResult:
        if self._initialized:
            raise RuntimeError("continuous harness session can run only once")
        self._initialized = True
        if resume_in_progress and not resume_existing_conversation:
            raise ValueError("in-progress resume requires an existing conversation")
        if resume_existing_conversation:
            if not self.conversation.messages:
                raise RuntimeError(
                    "serialized start context requires an initialized conversation"
                )
            if self._main_events_since_control and not (
                self.arm == "controlled"
                and (self.initial_control_packet is None or resume_in_progress)
            ):
                raise RuntimeError(
                    "initial main events require controlled bootstrap from a serialized prefix"
                )
        else:
            if self._main_events_since_control:
                raise RuntimeError(
                    "initial main events require a serialized conversation"
                )
            common_start = R.render_base_worker_prompt(self.current_packet)
            self.conversation.initialize(common_start)
        self.status = "running"

        if not resume_in_progress:
            if self.arm == "no-control":
                start = R.render_no_control_start_contract()
                self.conversation.append_user_turn(start)
                self.control_events.append(
                    {
                        "controller_called": False,
                        "packet_generated": False,
                        "message_index": len(self.conversation.messages) - 1,
                    }
                )
            elif self.initial_control_packet is None:
                start = R.render_controlled_bootstrap_contract()
                self.conversation.append_user_turn(start)
                self.control_events.append(
                    {
                        "cycle_index": -1,
                        "bootstrap": True,
                        "controller_called": False,
                        "packet_generated": False,
                        "message_index": len(self.conversation.messages) - 1,
                    }
                )
            else:
                if not resume_existing_conversation:
                    raise RuntimeError(
                        "initial controller packet requires serialized cutpoint context"
                    )
                if not self._control_channel_capacity_available():
                    return self._result()
                applied = self._apply_contract(
                    self.current_packet,
                    0,
                    packet_generated=False,
                    initial_replay_control=True,
                )
                if not applied:
                    return self._result()

        if self._resume_state:
            if not resume_in_progress:
                raise RuntimeError(
                    "controlled session recovery requires in-progress resume"
                )
            cycle_index = int(self._resume_state.get("cycle_index") or 0)
            next_action = self._next_action
        else:
            cycle_index = 0
            next_action = "call_worker"
        if self.fixed_control and next_action == "call_reporter":
            # Resume older fixed-control attempts from the same safe boundary
            # without invoking the Reporter or changing the Worker conversation.
            next_action = "call_controller"
            self._pending_reporter_state = None
        while self.status == "running":
            if next_action == "call_reporter":
                if not self._control_channel_capacity_available():
                    break
                reported = self._report_and_compile(
                    cycle_index,
                    reporter_resume_state=self._pending_reporter_state,
                )
                self._pending_reporter_state = None
                if not reported:
                    break
                next_action = "call_controller"
                continue
            if next_action == "call_controller":
                if not self._control_channel_capacity_available():
                    break
                applied = self._apply_contract(
                    self.current_packet,
                    cycle_index,
                    packet_generated=not self.fixed_control,
                )
                if not applied:
                    break
                next_action = "call_worker"
                continue
            if next_action == "apply_contract":
                if not isinstance(self._pending_controller_state, dict):
                    raise RuntimeError(
                        "apply-contract recovery is missing the pending result"
                    )
                applied = self._apply_contract(
                    self.current_packet,
                    cycle_index,
                    packet_generated=True,
                    pending_controller_state=self._pending_controller_state,
                )
                self._pending_controller_state = None
                if not applied:
                    break
                next_action = "call_worker"
                continue
            if next_action != "call_worker":
                raise RuntimeError(f"unsupported recovery action: {next_action}")
            remaining = self.max_main_worker_turns - self.conversation.total_model_calls
            if remaining <= 0:
                self.status = "budget_exhausted"
                self.termination_reason = "main_worker_budget_exhausted"
                break
            remaining_wall_time = None
            if self.wall_time_limit_sec is not None:
                remaining_wall_time = max(
                    0.0,
                    float(self.wall_time_limit_sec)
                    - self._main_worker_wall_time_used_sec,
                )
                if remaining_wall_time <= 0:
                    self.status = "runtime_exceeded"
                    self.termination_reason = "main_worker_wall_time_exhausted"
                    break
            slice_started = time.monotonic()
            base_model_calls = self.conversation.total_model_calls
            base_repo_actions = self.conversation.total_repo_actions

            def save_main_progress(progress: dict[str, Any]) -> None:
                if self.main_progress_sink is None:
                    return
                row = copy.deepcopy(progress)
                prefix = self._resume_main_slice_prefix
                current_slice_turns = int(prefix.get("main_worker_turns") or 0) + int(
                    progress.get("main_worker_turns") or 0
                )
                current_slice_repo_actions = int(
                    prefix.get("repo_tool_calls") or 0
                ) + int(progress.get("repo_tool_calls") or 0)
                row["main_worker_turns"] = base_model_calls + int(
                    progress.get("main_worker_turns") or 0
                )
                row["repo_tool_calls"] = base_repo_actions + int(
                    progress.get("repo_tool_calls") or 0
                )
                row["control_decision"] = self._current_worker_action
                row["control_events"] = copy.deepcopy(self.control_events)
                row["safe_to_resume"] = True
                row["current_slice_main_worker_turns"] = current_slice_turns
                row["current_slice_repo_tool_calls"] = current_slice_repo_actions
                row["tool_events"] = [
                    *copy.deepcopy(prefix.get("tool_events") or []),
                    *list(progress.get("tool_events") or []),
                ]
                row["model_response_audit"] = [
                    *copy.deepcopy(prefix.get("model_response_audit") or []),
                    *list(progress.get("model_response_audit") or []),
                ]
                row["wall_time_sec"] = round(
                    self._main_worker_wall_time_used_sec
                    + float(progress.get("wall_time_sec") or 0.0),
                    6,
                )
                if self.arm == "controlled":
                    row["phase"] = "call_worker"
                    row["controlled_session_state"] = self._session_recovery_state(
                        cycle_index,
                        "call_worker",
                        main_worker_turns=row["main_worker_turns"],
                        repo_tool_calls=row["repo_tool_calls"],
                        main_worker_wall_time_sec=row["wall_time_sec"],
                    )
                self.main_progress_sink(cycle_index, row)

            save_main_progress(
                {
                    "messages": copy.deepcopy(self.conversation.messages),
                    "main_worker_turns": 0,
                    "repo_tool_calls": 0,
                    "tool_events": [],
                }
            )
            main_kwargs: dict[str, Any] = {
                "arm": self.arm,
                "control_decision": self._current_worker_action,
                "turns_remaining": remaining,
                "seed": self.seed,
                "wall_time_limit_sec": remaining_wall_time,
            }
            if self.main_progress_sink is not None:
                main_kwargs["progress_sink"] = save_main_progress
            if resume_in_progress and self._resume_main_slice_prefix:
                main_kwargs["initial_empty_responses"] = int(
                    self._resume_main_slice_prefix.get("consecutive_empty_responses")
                    or 0
                )
                main_kwargs["initial_protocol_errors"] = list(
                    self._resume_main_slice_prefix.get("protocol_errors") or []
                )
            main_slice = self.conversation.run_main_until_boundary(
                **main_kwargs,
            )
            if resume_in_progress and self._resume_main_slice_prefix:
                prefix = self._resume_main_slice_prefix
                main_slice["main_worker_turns"] = int(
                    prefix.get("main_worker_turns") or 0
                ) + int(main_slice.get("main_worker_turns") or 0)
                main_slice["repo_tool_calls"] = int(
                    prefix.get("repo_tool_calls") or 0
                ) + int(main_slice.get("repo_tool_calls") or 0)
                main_slice["tool_events"] = [
                    *copy.deepcopy(prefix.get("tool_events") or []),
                    *list(main_slice.get("tool_events") or []),
                ]
                main_slice["model_response_audit"] = [
                    *copy.deepcopy(prefix.get("model_response_audit") or []),
                    *list(main_slice.get("model_response_audit") or []),
                ]
                self._resume_main_slice_prefix = {}
            slice_elapsed = time.monotonic() - slice_started
            self._main_worker_wall_time_used_sec += slice_elapsed
            main_slice["session_main_worker_wall_time_elapsed_sec"] = round(
                self._main_worker_wall_time_used_sec, 6
            )
            self.main_worker_slices.append(copy.deepcopy(main_slice))
            self._main_events_since_control.extend(main_slice.get("tool_events") or [])
            slice_status = str(main_slice.get("status") or "")
            if self.arm == "no-control" or slice_status != "inner_loop_completed":
                reason = str(
                    main_slice.get("termination_reason") or "main_worker_terminated"
                )
                self.status = slice_status or "runtime_failure"
                self.termination_reason = reason
                self.terminal_action = (
                    "natural_completion"
                    if self.arm == "no-control"
                    and self.termination_reason == "natural_completion"
                    else ""
                )
                break
            if self.conversation.total_model_calls >= self.max_main_worker_turns:
                self.status = "budget_exhausted"
                self.termination_reason = (
                    "main_worker_budget_exhausted_at_inner_loop_completion"
                )
                break
            if not self._control_channel_capacity_available():
                break
            cycle_index += 1
            next_action = "call_controller" if self.fixed_control else "call_reporter"
            self._save_recovery_boundary(cycle_index, next_action)
        return self._result()

    def _result(self) -> ContinuousSessionResult:
        return ContinuousSessionResult(
            arm=self.arm,
            status=self.status,
            termination_reason=self.termination_reason,
            terminal_action=self.terminal_action,
            packets=copy.deepcopy(self.packets),
            reporter_runs=copy.deepcopy(self.reporter_runs),
            main_worker_slices=copy.deepcopy(self.main_worker_slices),
            control_events=copy.deepcopy(self.control_events),
            controller_results=list(self.controller_results),
            controller_conversation=copy.deepcopy(self._controller_conversation),
            main_worker_turns=self.conversation.total_model_calls,
            main_worker_repo_tool_calls=self.conversation.total_repo_actions,
            reporter_turns=sum(
                int(run.get("reporter_turns") or 0) for run in self.reporter_runs
            ),
            controller_calls=len(self.controller_results),
            main_worker_wall_time_sec=round(self._main_worker_wall_time_used_sec, 6),
            control_channel_wall_time_sec=round(
                self._control_channel_wall_time_used_sec, 6
            ),
        )


__all__ = [
    "ContinuousHarnessSession",
    "ContinuousSessionResult",
    "CONTROL_CHANNEL_WALL_TIME_LIMIT_SEC",
    "CONTROL_CYCLE_LIMIT",
    "MAIN_WORKER_TURN_LIMIT",
    "REPORTER_TURN_LIMIT",
    "validate_reporter_shape",
]
