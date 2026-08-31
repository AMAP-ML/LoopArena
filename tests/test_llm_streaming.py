from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from looparena.runtime.controller_client import ControllerChatClient
from looparena.runtime.llm import WorkerClient, _read_chat_completion_stream


def _sse(*events: object, done: bool = True) -> list[bytes]:
    lines: list[bytes] = []
    for event in events:
        lines.append(f"data: {json.dumps(event, ensure_ascii=False)}\n".encode("utf-8"))
        lines.append(b"\n")
    if done:
        lines.extend((b"data: [DONE]\n", b"\n"))
    return lines


class _Response:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class StreamingGatewayTest(unittest.TestCase):
    def test_model_controller_uses_streaming(self) -> None:
        controller = ControllerChatClient(
            model="qwen3.7-plus",
            base_url="https://gateway.invalid/v1",
            temperature=0,
            timeout=30,
            credential_profile_id="test",
        )

        self.assertTrue(controller.client.streaming)

    def test_sse_reassembles_tool_calls_reasoning_and_usage(self) -> None:
        chunks = _sse(
            {
                "id": "response-1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": "inspect ",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "create",
                                        "arguments": '{"path":"a.py",',
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "response-1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "reasoning_content": "then write",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"content":"x"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "total_tokens": 19,
                },
            },
        )

        result = _read_chat_completion_stream(_Response(chunks))

        self.assertEqual(result["response_id"], "response-1")
        self.assertEqual(result["finish_reason"], "tool_calls")
        self.assertEqual(result["choice_count"], 1)
        self.assertEqual(result["message"]["reasoning_content"], "inspect then write")
        self.assertEqual(
            result["message"]["tool_calls"],
            [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "create",
                        "arguments": '{"path":"a.py","content":"x"}',
                    },
                }
            ],
        )
        self.assertEqual(result["usage"]["total_tokens"], 19)

    def test_sse_rejects_an_incomplete_stream(self) -> None:
        chunks = _sse(
            {
                "id": "response-1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "partial"},
                        "finish_reason": None,
                    }
                ],
            },
            done=False,
        )

        with self.assertRaisesRegex(RuntimeError, "missing_done"):
            _read_chat_completion_stream(_Response(chunks))

    def test_worker_streaming_uses_http_sse_and_preserves_audit(self) -> None:
        chunks = _sse(
            {
                "id": "response-2",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "PONG"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["accept"] = request.get_header("Accept")
            captured["timeout"] = timeout
            return _Response(chunks)

        client = WorkerClient(
            model="qwen3.7-plus",
            api_key_env="LOOPARENA_TEST_GATEWAY_KEY",
            allow_api_key_fallback=False,
            base_url="https://gateway.invalid/v1",
            timeout_sec=37,
            transport="http",
            streaming=True,
        )
        with (
            mock.patch.dict(
                os.environ,
                {"LOOPARENA_TEST_GATEWAY_KEY": "test-only-key"},
                clear=False,
            ),
            mock.patch(
                "looparena.runtime.llm.urllib.request.urlopen", side_effect=fake_urlopen
            ),
        ):
            result = client.chat(
                [{"role": "user", "content": "ping"}],
                temperature=0,
                max_tokens=8,
                extra_body={"enable_thinking": False},
            )

        self.assertEqual(result["content"], "PONG")
        self.assertEqual(result["_looparena_response_audit"]["transport"], "http-sse")
        self.assertEqual(
            result["_looparena_response_audit"]["usage"]["total_tokens"], 6
        )
        self.assertEqual(captured["accept"], "text/event-stream")
        self.assertEqual(captured["timeout"], 37)
        payload = captured["payload"]
        assert isinstance(payload, dict)
        self.assertIs(payload["stream"], True)
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertIs(payload["enable_thinking"], False)


if __name__ == "__main__":
    unittest.main()
