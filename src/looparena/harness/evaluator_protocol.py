"""Schemas and deterministic classification for terminal benchmark evaluation."""

from __future__ import annotations

from typing import Any

EVALUATOR_PLAN_SCHEMA = "looparena.terminal_evaluator_plan.v1"

OFFICIAL_ADAPTERS = {"scbench", "beyondswe_harbor"}
OFFICIAL_ADAPTER_VERSIONS = {
    "scbench": "official-eval-snapshot-v1",
    "beyondswe_harbor": "harbor-verifier-v1",
}
_COUNTABLE_TERMINAL_STATUSES = {
    "completed",
    "budget_exhausted",
    "runtime_exceeded",
    "protocol_violation",
    "invalid_contract",
    "control_channel_budget_exhausted",
    "reporter_budget_exhausted",
    "reporter_protocol_failure",
}
_INFRASTRUCTURE_INVALID_REASONS = {
    "controller_provider_failure",
    "controller_context_preflight_exhausted",
    "context_capacity_exhausted",
    "main_worker_gateway_timeout",
    "main_worker_provider_failure",
    "main_worker_wall_time_exhausted",
    "reporter_context_capacity_exhausted",
    "reporter_gateway_timeout",
    "reporter_provider_failure",
    "reporter_sandbox_failure",
    "reporter_wall_time_exhausted",
}
_PROTOCOL_INVALID_STATUSES = {"protocol_violation", "invalid_contract"}
_PROTOCOL_INVALID_REASONS = {
    "invalid_contract",
    "empty_assistant_response_limit_reached",
    "multiple_tool_calls_not_supported",
}
_BUDGET_TERMINATIONS = {
    "main_worker_budget_exhausted",
    "main_worker_budget_exhausted_at_inner_loop_completion",
}


def classify_infrastructure_validity(
    status: object,
    termination_reason: object,
) -> dict[str, Any]:
    """Classify whether a run measured the intended model/protocol attempt.

    Worker or reporter budget exhaustion, reporter protocol failure, worker
    protocol violations, and invalid controller contracts are countable
    benchmark outcomes under a frozen policy. Provider, context-capacity, and
    harness infrastructure failures and wall-time exhaustion are not.
    """

    status_text = str(status or "")
    reason_text = str(termination_reason or "")
    valid = (
        status_text in _COUNTABLE_TERMINAL_STATUSES
        and reason_text not in _INFRASTRUCTURE_INVALID_REASONS
    )
    if valid:
        classification = "valid_attempt"
    elif reason_text in _INFRASTRUCTURE_INVALID_REASONS:
        classification = reason_text
    else:
        classification = status_text or "missing_status"
    return {
        "valid": valid,
        "classification": classification,
    }


def derive_task_outcome(
    manifest: dict[str, Any],
    evaluator: object,
) -> dict[str, Any]:
    """Derive task success only from the terminal evaluator and worker budget."""

    passed = evaluator.get("passed") if isinstance(evaluator, dict) else None
    turns = manifest.get("main_worker_turns")
    budget = manifest.get("main_worker_turn_budget")
    within_budget = (
        isinstance(turns, int)
        and not isinstance(turns, bool)
        and isinstance(budget, int)
        and not isinstance(budget, bool)
        and 0 <= turns <= budget
    )
    success = bool(passed is True and within_budget)
    return {
        "evaluator_attached": isinstance(evaluator, dict),
        "terminal_evaluator_passed": passed if isinstance(passed, bool) else None,
        "within_main_worker_turn_budget": within_budget,
        "success_at_budget": success if isinstance(passed, bool) else None,
    }


def derive_protocol_status(manifest: dict[str, Any]) -> dict[str, bool]:
    """Classify protocol validity separately from legal episode termination."""

    status = str(manifest.get("status") or "")
    reason = str(manifest.get("termination_reason") or "")
    protocol_valid = (
        status not in _PROTOCOL_INVALID_STATUSES
        and reason not in _PROTOCOL_INVALID_REASONS
    )
    arm = manifest.get("arm")
    submit_protocol_satisfied = (
        (status == "completed" and arm == "controlled" and reason == "controller_stop")
        or (
            status == "completed"
            and arm == "no-control"
            and reason == "natural_completion"
        )
        or (status == "budget_exhausted" and reason in _BUDGET_TERMINATIONS)
    )
    return {
        "protocol_valid": protocol_valid,
        "submit_protocol_satisfied": submit_protocol_satisfied,
    }


def evaluator_identity(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a small, path-free description of the selected evaluator."""

    pass_policy = (
        "reward-equals-one"
        if plan.get("adapter_kind") == "beyondswe_harbor"
        else (
            plan.get("pass_policy")
            or (plan.get("adapter_config") or {}).get("pass_policy")
            or "all-cases"
        )
    )
    identity = {
        "adapter_kind": plan.get("adapter_kind"),
        "adapter_version": plan.get("adapter_version"),
        "source_revision": plan.get("source_revision"),
        "runtime_identity": plan.get("runtime_identity"),
        "pass_policy": pass_policy,
        "network": plan.get("network"),
        "timeout_sec": plan.get("timeout_sec"),
    }
    evaluator_revision = plan.get("evaluator_revision")
    if isinstance(evaluator_revision, str) and evaluator_revision.strip():
        identity["evaluator_revision"] = evaluator_revision.strip()
    return identity


def validate_evaluator_plan(plan: object) -> list[str]:
    """Check only fields needed to select and safely run an adapter."""

    if not isinstance(plan, dict):
        return ["evaluator_plan_must_be_object"]
    errors: list[str] = []
    required = {"adapter_kind", "adapter_config"}
    for field in sorted(required - set(plan)):
        errors.append(f"evaluator_plan_missing:{field}")
    if plan.get("adapter_kind") not in OFFICIAL_ADAPTERS:
        errors.append("evaluator_adapter_kind_invalid")
    if "network" in plan and plan.get("network") not in {"none", "bridge", "host"}:
        errors.append("evaluator_network_invalid")
    if "solve_network" in plan and plan.get("solve_network") not in {
        "none",
        "bridge",
        "host",
    }:
        errors.append("solve_network_invalid")
    timeout = plan.get("timeout_sec")
    if "timeout_sec" in plan and (
        isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0
    ):
        errors.append("evaluator_timeout_invalid")
    if not isinstance(plan.get("adapter_config"), dict):
        errors.append("evaluator_adapter_config_invalid")
    return errors


def validate_evaluation_receipt(
    receipt: object,
    *,
    plan: dict[str, Any],
) -> list[str]:
    """Check only that an evaluator result is countable and unambiguous."""

    if not isinstance(receipt, dict):
        return ["evaluation_receipt_must_be_object"]
    errors: list[str] = []
    required = {
        "adapter_kind",
        "execution_status",
        "infrastructure_failure",
        "task_passed",
        "solve_sandbox_stopped_before_evaluator",
    }
    for field in sorted(required - set(receipt)):
        errors.append(f"evaluation_receipt_missing:{field}")
    if receipt.get("adapter_kind") != plan.get("adapter_kind"):
        errors.append("evaluation_receipt_adapter_kind_mismatch")
    if (
        "model_access_ended_before_evaluator" in receipt
        and receipt.get("model_access_ended_before_evaluator") is not True
    ):
        errors.append("evaluation_receipt_model_boundary_invalid")
    sandbox_stopped = receipt.get("solve_sandbox_stopped_before_evaluator")
    if not isinstance(sandbox_stopped, bool):
        errors.append("evaluation_receipt_sandbox_boundary_invalid")
    elif (
        sandbox_stopped is False
        and receipt.get("model_access_ended_before_evaluator") is not True
    ):
        # Old receipts without the newer field remain valid only for the old
        # post-teardown evaluator boundary. A shared live verifier must state
        # explicitly that every model-facing call ended before test injection.
        errors.append("evaluation_receipt_live_model_boundary_missing")
    status = receipt.get("execution_status")
    if not isinstance(status, str) or not status.strip():
        errors.append("evaluation_execution_status_invalid")
    infrastructure_failure = receipt.get("infrastructure_failure")
    if not isinstance(infrastructure_failure, bool):
        errors.append("evaluation_infrastructure_failure_invalid")
    elif (status == "completed") != (infrastructure_failure is False):
        errors.append("evaluation_status_infrastructure_mismatch")
    task_passed = receipt.get("task_passed")
    if "passed" in receipt and receipt.get("passed") != task_passed:
        errors.append("evaluation_passed_alias_mismatch")
    if status == "completed" and infrastructure_failure is False:
        if not isinstance(task_passed, bool):
            errors.append("healthy_evaluation_requires_task_passed_boolean")
    elif task_passed is not None:
        errors.append("invalid_evaluation_requires_null_task_passed")
    return errors


def combine_final_infrastructure_validity(
    solve_validity: object,
    receipt: object,
) -> dict[str, Any]:
    solve_valid = (
        isinstance(solve_validity, dict) and solve_validity.get("valid") is True
    )
    evaluator_valid = (
        isinstance(receipt, dict)
        and receipt.get("execution_status") == "completed"
        and receipt.get("infrastructure_failure") is False
    )
    valid = solve_valid and evaluator_valid
    if not solve_valid:
        classification = "solve_infrastructure_invalid"
    elif not evaluator_valid:
        classification = "evaluator_infrastructure_invalid"
    else:
        classification = "valid_attempt"
    return {
        "valid": valid,
        "classification": classification,
        "solve_valid": solve_valid,
        "evaluator_valid": evaluator_valid,
    }


def derive_final_task_outcome(
    manifest: dict[str, Any],
    receipt: object,
) -> dict[str, Any]:
    turns = manifest.get("main_worker_turns")
    budget = manifest.get("main_worker_turn_budget")
    within_budget = (
        isinstance(turns, int)
        and not isinstance(turns, bool)
        and isinstance(budget, int)
        and not isinstance(budget, bool)
        and 0 <= turns <= budget
    )
    final_validity = combine_final_infrastructure_validity(
        manifest.get("solve_infrastructure_validity")
        or manifest.get("infrastructure_validity"),
        receipt,
    )
    protocol = derive_protocol_status(manifest)
    task_passed = receipt.get("task_passed") if isinstance(receipt, dict) else None
    success = (
        task_passed is True
        and within_budget
        and protocol["protocol_valid"]
        and protocol["submit_protocol_satisfied"]
        if final_validity["valid"]
        else None
    )
    return {
        "evaluator_attached": isinstance(receipt, dict),
        "terminal_evaluator_passed": task_passed
        if isinstance(task_passed, bool)
        else None,
        "within_main_worker_turn_budget": within_budget,
        **protocol,
        "success_at_budget": success,
    }


__all__ = [
    "EVALUATOR_PLAN_SCHEMA",
    "OFFICIAL_ADAPTERS",
    "OFFICIAL_ADAPTER_VERSIONS",
    "classify_infrastructure_validity",
    "combine_final_infrastructure_validity",
    "derive_final_task_outcome",
    "derive_protocol_status",
    "derive_task_outcome",
    "evaluator_identity",
    "validate_evaluation_receipt",
    "validate_evaluator_plan",
]
