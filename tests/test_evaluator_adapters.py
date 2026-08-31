from __future__ import annotations

import json
import os
import sys
from unittest import mock

from looparena.evaluators.base import (
    base_receipt,
    beyondswe_evaluator_network,
    run_argv,
    scbench_evaluator_network,
)
from looparena.evaluators.beyondswe import _pytest_counts
from looparena.evaluators.scbench import (
    _official_result_count_evidence,
    _passes_policy,
)


def test_scbench_result_evidence_uses_the_frozen_denominator() -> None:
    report = {
        "tests": {
            "core": ["test_a", "test_b"],
            "regression": ["test_c"],
        },
        "total_counts": {"core": 2, "regression": 1},
        "pass_counts": {"core": 2, "regression": 1},
    }
    assert _official_result_count_evidence(report, expected=3) == (3, 3, True)
    assert _official_result_count_evidence(report, expected=4) == (3, 3, False)
    assert _passes_policy(report, "all-cases") is True
    report["pass_counts"]["regression"] = 0
    assert _passes_policy(report, "all-cases") is False


def test_beyondswe_pytest_parser_ignores_numbers_outside_terminal_summary() -> None:
    output = """collected 3 items
corrupt patch at line 44
================ 2 passed, 1 failed in 0.4s ================
"""
    assert _pytest_counts(output, expected=3) == (3, 3)


def test_receipts_keep_task_failure_separate_from_infrastructure_failure() -> None:
    plan = {
        "adapter_kind": "scbench",
        "adapter_version": "test",
        "source_revision": "revision",
        "pass_policy": "all-cases",
    }
    task_failure = base_receipt(
        plan,
        status="completed",
        infrastructure_failure=False,
        task_passed=False,
        tests_expected=2,
        tests_collected=2,
        tests_executed=2,
    )
    infrastructure_failure = base_receipt(
        plan,
        status="setup_failed",
        infrastructure_failure=True,
        task_passed=None,
        tests_expected=2,
    )
    assert task_failure["task_passed"] is False
    assert task_failure["infrastructure_failure"] is False
    assert infrastructure_failure["task_passed"] is None
    assert infrastructure_failure["infrastructure_failure"] is True


def test_source_network_policies_are_normalized_without_running_docker() -> None:
    assert scbench_evaluator_network({"docker": {"network": "none"}}) == "none"
    assert beyondswe_evaluator_network({"environment": {"network_mode": "public"}}) == (
        "public",
        "bridge",
    )


def test_terminal_evaluator_subprocess_does_not_inherit_model_api_keys(
    tmp_path,
) -> None:
    script = (
        "import json, os; "
        "print(json.dumps({'api_key': os.environ.get('OPENAI_API_KEY'), "
        "'visible': os.environ.get('VISIBLE_SETTING')}))"
    )
    with mock.patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "must-not-leak", "VISIBLE_SETTING": "kept"},
        clear=False,
    ):
        code, stdout, _, _, timed_out = run_argv(
            [sys.executable, "-c", script], cwd=tmp_path, timeout_sec=10
        )
    assert code == 0
    assert timed_out is False
    assert json.loads(stdout) == {"api_key": None, "visible": "kept"}
