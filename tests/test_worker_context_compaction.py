from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from looparena.runtime.worker_session import (
    EXPIRED_TOOL_OUTPUT,
    WORKER_INPUT_MAX_UTF8_BYTES,
    _worker_input_utf8_bytes,
    _worker_visible_messages,
    WorkerConversation,
    run_forked_reporter,
    run_main_worker_until_boundary,
)
from looparena.runtime.worker_tools import MAIN_REPOSITORY_TOOLS


def _tool_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
    }


class WorkerContextCompactionTest(unittest.TestCase):
    def test_under_limit_returns_the_original_messages_unchanged(self) -> None:
        messages = [
            {"role": "system", "content": "Work on the repository."},
            {"role": "user", "content": "Fix the parser."},
        ]
        before = copy.deepcopy(messages)

        visible = _worker_visible_messages(messages, MAIN_REPOSITORY_TOOLS)

        self.assertIs(visible, messages)
        self.assertEqual(messages, before)

    def test_over_limit_masks_oldest_tool_output_and_keeps_recent_history(self) -> None:
        messages = [
            {"role": "system", "content": "Work on the repository."},
            {"role": "user", "content": "Fix the parser."},
            _tool_call("old-call"),
            {"role": "tool", "tool_call_id": "old-call", "content": "x" * 500},
            _tool_call("recent-call"),
            {
                "role": "tool",
                "tool_call_id": "recent-call",
                "content": "recent output must remain visible",
            },
        ]
        before = copy.deepcopy(messages)
        expected = copy.deepcopy(messages)
        expected[3]["content"] = EXPIRED_TOOL_OUTPUT
        limit = _worker_input_utf8_bytes(expected, MAIN_REPOSITORY_TOOLS)

        visible = _worker_visible_messages(
            messages,
            MAIN_REPOSITORY_TOOLS,
            max_utf8_bytes=limit,
        )

        self.assertEqual(messages, before)
        self.assertIsNot(visible, messages)
        self.assertEqual(visible, expected)
        self.assertEqual(visible[2]["tool_calls"][0]["id"], "old-call")
        self.assertEqual(visible[3]["tool_call_id"], "old-call")
        self.assertEqual(visible[5]["content"], "recent output must remain visible")
        self.assertLessEqual(
            _worker_input_utf8_bytes(visible, MAIN_REPOSITORY_TOOLS),
            limit,
        )

    def test_uncompactable_history_fails_before_the_provider_call(self) -> None:
        class WorkerStub:
            def __init__(self) -> None:
                self.calls = 0

            def chat(self, messages, tools, *, seed):
                del messages, tools, seed
                self.calls += 1
                raise AssertionError("provider must not be called")

        worker = WorkerStub()
        result = run_main_worker_until_boundary(
            worker,
            object(),
            Path("."),
            [
                {
                    "role": "system",
                    "content": "x" * WORKER_INPUT_MAX_UTF8_BYTES,
                }
            ],
            arm="no-control",
            turns_remaining=1,
        )

        self.assertEqual(worker.calls, 0)
        self.assertEqual(result["status"], "context_capacity_exhausted")
        self.assertEqual(result["termination_reason"], "context_capacity_exhausted")
        self.assertIn(
            "worker_history_compaction_insufficient",
            result["provider_error"],
        )

    def test_main_worker_sends_compacted_view_but_preserves_full_history(self) -> None:
        old_output = "x" * WORKER_INPUT_MAX_UTF8_BYTES
        messages = [
            {"role": "system", "content": "Work on the repository."},
            {"role": "user", "content": "Fix the parser."},
            _tool_call("old-call"),
            {"role": "tool", "tool_call_id": "old-call", "content": old_output},
            _tool_call("recent-call"),
            {
                "role": "tool",
                "tool_call_id": "recent-call",
                "content": "recent output must remain visible",
            },
        ]

        class WorkerStub:
            def __init__(self) -> None:
                self.received_messages: list[dict] | None = None

            def chat(self, received, tools, *, seed):
                del tools, seed
                self.received_messages = copy.deepcopy(received)
                return {
                    "role": "assistant",
                    "content": "Current cycle complete.",
                    "tool_calls": [],
                }

        worker = WorkerStub()
        result = run_main_worker_until_boundary(
            worker,
            object(),
            Path("."),
            messages,
            arm="no-control",
            turns_remaining=1,
        )

        self.assertIsNotNone(worker.received_messages)
        assert worker.received_messages is not None
        self.assertEqual(worker.received_messages[3]["content"], EXPIRED_TOOL_OUTPUT)
        self.assertEqual(
            worker.received_messages[5]["content"],
            "recent output must remain visible",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["messages"][3]["content"], old_output)
        self.assertEqual(messages[3]["content"], old_output)

    def test_completed_artifacts_do_not_repeat_the_worker_transcript(self) -> None:
        class WorkerStub:
            def chat(self, messages, tools, *, seed):
                del messages, tools, seed
                return {"role": "assistant", "content": "Done.", "tool_calls": []}

        conversation = WorkerConversation(WorkerStub(), object(), Path("."))
        conversation.initialize("Complete the task.")
        main_slice = conversation.run_main_until_boundary(
            arm="no-control",
            turns_remaining=1,
        )

        self.assertNotIn("messages", main_slice)
        self.assertEqual(main_slice["message_range"], [2, 3])
        self.assertEqual(conversation.messages[-1]["content"], "Done.")

        class ReporterStub:
            def chat(self, messages, tools, *, seed):
                del messages, tools, seed
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "report",
                            "type": "function",
                            "function": {
                                "name": "round_report",
                                "arguments": json.dumps({"summary": "Done."}),
                            },
                        }
                    ],
                }

        reporter_run = run_forked_reporter(
            ReporterStub(),
            object(),
            Path("."),
            conversation.messages,
            "A deterministic rendering of the Worker transcript.",
            max_reporter_turns=1,
        )
        self.assertNotIn("reporter_fork_messages", reporter_run)
        self.assertEqual(
            reporter_run["worker_transcript_message_count"],
            len(conversation.messages),
        )
        self.assertEqual(len(reporter_run["reporter_messages"]), 1)


if __name__ == "__main__":
    unittest.main()
