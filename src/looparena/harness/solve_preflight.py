"""Minimal solve-environment safety preflight."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any


def build_solve_environment_preflight(
    *,
    workspace: Path,
    sandbox: Any,
) -> dict[str, Any]:
    """Record the workspace and container boundary used for this solve."""

    workspace_available = Path(workspace).is_dir()
    security_audit = copy.deepcopy(getattr(sandbox, "container_security_audit", None))
    container_security_passed = (
        isinstance(security_audit, dict) and security_audit.get("status") == "passed"
    )
    checks = {
        "workspace_available": workspace_available,
        "container_security_passed": container_security_passed,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "image_identity": copy.deepcopy(getattr(sandbox, "image_identity", None)),
        "container_security_audit": security_audit,
    }


def validate_solve_environment_preflight(receipt: object) -> list[str]:
    """Accept a preflight iff its workspace and container-boundary checks passed."""

    if not isinstance(receipt, dict):
        return ["solve_environment_preflight_must_be_object"]
    checks = receipt.get("checks")
    if not isinstance(checks, dict):
        return ["solve_environment_preflight_checks_missing"]
    errors: list[str] = []
    if checks.get("workspace_available") is not True:
        errors.append("solve_environment_preflight_failed:workspace_available")
    if checks.get("container_security_passed") is not True:
        errors.append("solve_environment_preflight_failed:container_security_passed")
    if receipt.get("status") != "passed":
        errors.append("solve_environment_preflight_status_not_passed")
    return errors


__all__ = [
    "build_solve_environment_preflight",
    "validate_solve_environment_preflight",
]
