from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "tools"):
    sys.path.insert(0, str(path))

from looparena.commands.type1_run import DEFAULT_MAX_TOKENS, _evaluate_one, main
from looparena.harness.type1_benchmark import (
    TYPE1_SYSTEM_PROMPT,
    format_question,
    parse_choice,
    summarize,
)


class Type1BenchmarkTest(unittest.TestCase):
    def test_release_is_complete(self) -> None:
        root = ROOT / "benchmarks" / "type1"
        rows = [
            json.loads(line)
            for line in (root / "questions.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 90)
        self.assertEqual(manifest["items"], 90)
        self.assertEqual(manifest["release_status"], "released")
        self.assertEqual(
            [row["id"] for row in rows],
            [f"type1_{index:04d}" for index in range(1, 91)],
        )
        self.assertTrue(all(row["ideal"] in "ABCD" for row in rows))

    def test_parse_choice(self) -> None:
        self.assertEqual(parse_choice('{"choice":"c","rationale":"because"}'), "C")
        self.assertEqual(parse_choice("Answer: B"), "B")
        self.assertEqual(parse_choice("Reasoning.\n\n**Answer: D**"), "D")
        self.assertEqual(parse_choice("Reasoning about A and B.\n\nAnswer: C"), "C")
        self.assertEqual(parse_choice("D"), "D")
        self.assertIsNone(parse_choice("A or B could work"))

    def test_question_is_standalone_and_natural(self) -> None:
        prompt = format_question(
            {
                "task": "Fix the service.",
                "public_controller_packet": {
                    "allowed_actions": ["advance", "verify", "stop"],
                    "budget": {"remaining": 12},
                    "report_context": {"previous_action": "verify"},
                    "round_report": {
                        "work_history_and_current_state": "The patch is present.",
                        "verification_and_evidence": "One focused test passed.",
                        "open_issues_and_uncertainty": "The full suite has not run.",
                        "task_context_and_constraints": "Do not add dependencies.",
                    },
                    "quoted_worker_evidence": [{"evidence_ref": "E1"}],
                },
                "options": {letter: {"decision": letter} for letter in "ABCD"},
            }
        )
        self.assertTrue(prompt.startswith("# Latest coding-work report"))
        self.assertIn("## Overall repository task", prompt)
        self.assertIn("Fix the service.", prompt)
        self.assertIn("## Original coding-agent turns selected", prompt)
        self.assertIn("# Candidate control decisions", prompt)
        self.assertIn('## D\n\n{\n  "decision": "D"\n}', prompt)
        self.assertTrue(prompt.endswith("Answer: X"))
        self.assertNotIn("END OF PROMPT", prompt)
        self.assertIn("# Coding-work supervisor", TYPE1_SYSTEM_PROMPT)
        self.assertIn("### `advance`", TYPE1_SYSTEM_PROMPT)
        self.assertIn("### `verify`", TYPE1_SYSTEM_PROMPT)
        self.assertIn("### `stop`", TYPE1_SYSTEM_PROMPT)
        self.assertIn("## Your task", TYPE1_SYSTEM_PROMPT)
        self.assertNotIn("Type-I", TYPE1_SYSTEM_PROMPT)
        self.assertNotIn("## Response format", TYPE1_SYSTEM_PROMPT)

    def test_summary_counts_invalid_separately(self) -> None:
        self.assertEqual(
            summarize(
                [
                    {"correct": True, "prediction": "A"},
                    {"correct": False, "prediction": "B"},
                    {"correct": False, "prediction": None},
                    {"correct": False, "prediction": None, "error": "timeout"},
                ]
            ),
            {
                "items": 4,
                "scored_items": 3,
                "correct": 1,
                "incorrect": 1,
                "invalid": 1,
                "api_errors": 1,
                "complete": False,
                "accuracy": None,
                "observed_scored_accuracy": 1 / 3,
            },
        )

    def test_evaluate_one_sends_one_temperature_zero_request(self) -> None:
        calls = []

        class Client:
            def chat(self, messages, tools, **kwargs):
                calls.append({"messages": messages, "tools": tools, **kwargs})
                return {
                    "content": '{"choice":"B"}',
                    "_looparena_response_audit": {"transport": "test"},
                }

        question = {
            "id": "type1_0001",
            "input": [{"role": "user", "content": "q"}],
            "ideal": "B",
        }
        result = _evaluate_one(Client(), 100, question)
        self.assertTrue(result["correct"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["temperature"], 0)
        self.assertIsNone(calls[0]["seed"])
        self.assertIsNone(calls[0]["tools"])
        self.assertEqual(result["response_audit"], {"transport": "test"})

    def test_evaluate_one_redacts_provider_errors(self) -> None:
        secret = "sk-example-secret-that-must-not-leak"

        class Client:
            def chat(self, *_args, **_kwargs):
                raise RuntimeError(f"provider rejected {secret}")

        question = {
            "id": "type1_0001",
            "input": [{"role": "user", "content": "q"}],
            "ideal": "B",
        }
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
            result = _evaluate_one(Client(), 100, question)
        self.assertNotIn(secret, result["error"])
        self.assertIn("[REDACTED]", result["error"])

    def test_cli_returns_nonzero_when_api_calls_are_incomplete(self) -> None:
        question = {
            "id": "type1_0001",
            "input": [{"role": "user", "content": "q"}],
            "ideal": "B",
        }
        failed = {
            "id": "type1_0001",
            "response": "",
            "prediction": None,
            "ideal": "B",
            "correct": False,
            "error": "provider unavailable",
            "response_audit": None,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "questions.jsonl"
            output = root / "results.jsonl"
            data.write_text(json.dumps(question) + "\n", encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                mock.patch(
                    "looparena.commands.type1_run._evaluate_one", return_value=failed
                ),
            ):
                exit_code = main(
                    [
                        "--data",
                        str(data),
                        "--model",
                        "test-model",
                        "--output",
                        str(output),
                        "--concurrency",
                        "1",
                    ]
                )
        self.assertEqual(exit_code, 2)

    def test_gateway_default_output_budget_is_20480(self) -> None:
        self.assertEqual(DEFAULT_MAX_TOKENS, 20480)

    def test_preflight_validates_data_without_writing_output(self) -> None:
        question = {
            "id": "type1_0001",
            "input": [{"role": "user", "content": "q"}],
            "ideal": "B",
        }
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "questions.jsonl"
            data.write_text(json.dumps(question) + "\n", encoding="utf-8")
            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                mock.patch("sys.stdout", new_callable=io.StringIO) as stdout,
            ):
                exit_code = main(
                    [
                        "--data",
                        str(data),
                        "--model",
                        "test-model",
                        "--base-url",
                        "https://private-endpoint.example/v1",
                        "--preflight-only",
                    ]
                )
        self.assertEqual(exit_code, 0)
        receipt = json.loads(stdout.getvalue())
        self.assertEqual(receipt["base_url"], "[REDACTED]")
        self.assertNotIn("private-endpoint", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
