from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from looparena.commands.harness import _token_usage_summary
from looparena.runtime.controller_client import ControllerChatClient
from looparena.runtime.llm import WorkerClient


class _SseResponse:
    def __init__(self, model: str) -> None:
        events = [
            {
                "id": f"response-{model}",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        ]
        self.lines = [
            line
            for event in events
            for line in (f"data: {json.dumps(event)}\n".encode(), b"\n")
        ] + [b"data: [DONE]\n", b"\n"]

    def __enter__(self) -> _SseResponse:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class OpenAICompatibleTransportTest(unittest.TestCase):
    def test_model_families_use_openai_compatible_chat_completions(self) -> None:
        for model in ("qwen3.7-plus", "gpt-public-id", "claude-public-id"):
            with self.subTest(model=model):
                requests = []

                def urlopen(request, timeout):
                    requests.append(request)
                    return _SseResponse(model)

                client = WorkerClient(
                    model=model,
                    api_key_env="LOOPARENA_TEST_API_KEY",
                    allow_api_key_fallback=False,
                    base_url="https://api.example/v1",
                    transport="http",
                    streaming=True,
                )
                with (
                    mock.patch.dict(
                        os.environ,
                        {"LOOPARENA_TEST_API_KEY": "test-key"},
                        clear=False,
                    ),
                    mock.patch(
                        "looparena.runtime.llm.urllib.request.urlopen",
                        side_effect=urlopen,
                    ),
                ):
                    result = client.chat([{"role": "user", "content": "ping"}])

                self.assertEqual(result["content"], "ok")
                self.assertEqual(
                    requests[0].full_url,
                    "https://api.example/v1/chat/completions",
                )
                self.assertNotIn("x-session-id", requests[0].headers)

    def test_attempt_limit_one_makes_one_request(self) -> None:
        client = WorkerClient(
            model="qwen3.7-plus",
            api_key_env="LOOPARENA_TEST_API_KEY",
            allow_api_key_fallback=False,
            base_url="https://api.example/v1",
            transport="http",
            gateway_attempt_limit=1,
        )
        with (
            mock.patch.dict(
                os.environ,
                {"LOOPARENA_TEST_API_KEY": "test-key"},
                clear=True,
            ),
            mock.patch.object(
                client,
                "_chat_with_http",
                side_effect=RuntimeError("temporary transport failure"),
            ) as request,
        ):
            with self.assertRaisesRegex(RuntimeError, "authorized credential retries"):
                client.chat([{"role": "user", "content": "ping"}])
        self.assertEqual(request.call_count, 1)

    def test_controller_uses_provider_default_sampling_when_required(self) -> None:
        for model in ("gpt-public-id", "claude-public-id"):
            with self.subTest(model=model):
                controller = ControllerChatClient(
                    model=model,
                    base_url="https://api.example/v1",
                    temperature=0,
                    timeout=30,
                    credential_profile_id="test",
                    max_retries=1,
                    seed=7,
                    transport="http",
                )
                with mock.patch.object(
                    controller.client,
                    "chat",
                    return_value={"content": "{}", "_looparena_response_audit": {}},
                ) as chat:
                    controller([{"role": "user", "content": "continue"}])
                self.assertIsNone(chat.call_args.kwargs["seed"])
                self.assertIsNone(chat.call_args.kwargs["temperature"])

    def test_cache_tokens_are_aggregated_when_reported(self) -> None:
        summary = _token_usage_summary(
            [
                {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 5,
                        "total_tokens": 105,
                        "cached_prompt_tokens": 80,
                    }
                },
                {
                    "usage": {
                        "prompt_tokens": 70,
                        "completion_tokens": 6,
                        "total_tokens": 76,
                        "cached_prompt_tokens": 40,
                    }
                },
            ]
        )
        self.assertEqual(summary["total_tokens"], 181)
        self.assertEqual(summary["cached_prompt_tokens"], 120)


if __name__ == "__main__":
    unittest.main()
