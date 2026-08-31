#!/usr/bin/env python3
"""OpenAI-compatible clients used by the LoopArena harness.

Models share one harness-facing chat and tool interface. SDK-level automatic retries are
disabled; LoopArena retries at the same model-call boundary so the exact
conversation and workspace survive transient rate limits or connection resets.

Secrets come from the environment / a gitignored `.env`; raw keys are never logged.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from looparena.paths import repository_root


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs from a gitignored .env into os.environ (no override)."""
    path = path or (repository_root() / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()

DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"


def provider_family(model: str) -> str:
    """Return the model family used for provider-default sampling."""

    normalized = str(model or "").strip().lower().rsplit("/", 1)[-1]
    if normalized.startswith("gpt-"):
        return "gpt"
    if normalized.startswith("claude-"):
        return "claude"
    return ""


def default_worker_base_url() -> str:
    """Resolve the public OpenAI-compatible endpoint used by the Worker."""

    return os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"


DEFAULT_WORKER_MODEL = os.environ.get("LOOPARENA_WORKER_MODEL", "qwen3.7-plus")
DEFAULT_WORKER_BASE_URL = default_worker_base_url()
# This is only a local memory-safety guard. It is deliberately much larger
# than ordinary LoopArena histories so the harness does not invent an
# experimental stopping condition before the model provider's real context
# window is reached.
DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES = 64 * 1024 * 1024
# A transient gateway failure is retried at the same model-call boundary.  The
# caller's conversation and workspace therefore remain untouched.  When the
# scheduler supplies a second authorized credential, each credential gets the
# same three attempts before the call is reported as a provider failure.
WORKER_GATEWAY_ATTEMPT_LIMIT = 3
EXPLICIT_FALLBACK_API_KEY_ENV = "LOOPARENA_GATEWAY_FALLBACK_API_KEY"
EXPLICIT_FALLBACK_PROFILE_ENV = "LOOPARENA_GATEWAY_FALLBACK_PROFILE_ID"


def _redact(text: str) -> str:
    for env_name in (
        EXPLICIT_FALLBACK_API_KEY_ENV,
        "DASHSCOPE_API_KEY",
        "QWEN_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        value = os.environ.get(env_name)
        if value:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"sk-[A-Za-z0-9._-]{16,}", "[REDACTED]", text or "")
    text = re.sub(
        r"(api[_-]?key|authorization|bearer|access[_-]?token|refresh[_-]?token)"
        r"['\":= ]+[^\s,'\"]+",
        r"\1=[REDACTED]",
        text,
        flags=re.I,
    )
    text = re.sub(r"/Users/[A-Za-z0-9_.:/\\-]+", "[local-path]", text)
    return text


def _gateway_error_is_non_retryable(exc: Exception) -> bool:
    """Recognize request-shape/authentication failures hidden by gateway 5xx."""

    status_code = getattr(exc, "status_code", None)
    if status_code in {400, 401, 403, 404, 405, 422}:
        return True
    body = getattr(exc, "body", None)
    try:
        body_text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        body_text = str(body or "")
    text = f"{type(exc).__name__}: {exc}\n{body_text}".lower()
    return any(
        marker in text
        for marker in (
            "invalidparameter",
            "invalid_parameter",
            "invalid request",
            "invalid_request",
            "content field is a required field",
            "authentication",
            "unauthorized",
            "permission denied",
            "model not found",
        )
    )


def _gateway_error_is_credential_specific(exc: Exception) -> bool:
    """Return true when another authorized credential may fix the request."""

    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return True
    body = getattr(exc, "body", None)
    try:
        body_text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        body_text = str(body or "")
    text = f"{type(exc).__name__}: {exc}\n{body_text}".lower()
    return any(
        marker in text
        for marker in (
            "authentication",
            "unauthorized",
            "permission denied",
            "access denied",
            "does not have permission",
        )
    )


def _read_chat_completion_stream(response: Any) -> dict[str, Any]:
    """Collect one OpenAI-compatible SSE response without exposing partial output."""

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    response_id = ""
    finish_reason = ""
    usage: Any = None
    choice_indices: set[int] = set()
    saw_data = False
    saw_done = False
    event_data: list[str] = []

    def consume(data_text: str) -> None:
        nonlocal response_id, finish_reason, usage, saw_data, saw_done
        if data_text == "[DONE]":
            saw_done = True
            return
        try:
            chunk = json.loads(data_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("gateway_stream_invalid_json") from exc
        if not isinstance(chunk, dict):
            raise RuntimeError("gateway_stream_invalid_chunk")
        gateway_error = chunk.get("error")
        if isinstance(gateway_error, dict):
            code = str(gateway_error.get("code") or "unknown")
            message = _redact(str(gateway_error.get("message") or "unknown error"))
            raise RuntimeError(
                f"gateway_configuration_error:code={code}:message={message[:400]}"
            )
        saw_data = True
        if chunk.get("id") and not response_id:
            response_id = str(chunk["id"])
        if chunk.get("usage") is not None:
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not isinstance(choices, list):
            raise RuntimeError("gateway_stream_invalid_choices")
        for fallback_index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            raw_index = choice.get("index", fallback_index)
            choice_index = raw_index if isinstance(raw_index, int) else fallback_index
            choice_indices.add(choice_index)
            if choice_index != 0:
                continue
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            if delta.get("content"):
                content_parts.append(str(delta["content"]))
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if reasoning:
                reasoning_parts.append(str(reasoning))
            raw_tool_calls = delta.get("tool_calls") or []
            if not isinstance(raw_tool_calls, list):
                raise RuntimeError("gateway_stream_invalid_tool_calls")
            for fallback_tool_index, tool_call in enumerate(raw_tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                raw_tool_index = tool_call.get("index", fallback_tool_index)
                tool_index = (
                    raw_tool_index
                    if isinstance(raw_tool_index, int)
                    else fallback_tool_index
                )
                target = tool_calls.setdefault(
                    tool_index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if tool_call.get("id") and not target["id"]:
                    target["id"] = str(tool_call["id"])
                if tool_call.get("type"):
                    target["type"] = str(tool_call["type"])
                function = tool_call.get("function") or {}
                if not isinstance(function, dict):
                    continue
                if function.get("name") and not target["function"]["name"]:
                    target["function"]["name"] = str(function["name"])
                if function.get("arguments"):
                    target["function"]["arguments"] += str(function["arguments"])

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if event_data:
                consume("\n".join(event_data))
                event_data = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            event_data.append(line[5:].lstrip())
    if event_data:
        consume("\n".join(event_data))

    if not saw_data:
        raise RuntimeError("gateway_stream_invalid:no_data")
    if not saw_done:
        raise RuntimeError("gateway_stream_incomplete:missing_done")
    if not choice_indices:
        raise RuntimeError("gateway_stream_invalid:no_choices")
    return {
        "message": {
            "content": "".join(content_parts),
            "reasoning_content": "".join(reasoning_parts),
            "tool_calls": [tool_calls[index] for index in sorted(tool_calls)],
        },
        "finish_reason": finish_reason,
        "response_id": response_id,
        "choice_count": len(choice_indices),
        "usage": usage,
    }


# --------------------------------------------------------------------------- #
# Worker: Qwen via OpenAI-compatible gateway
# --------------------------------------------------------------------------- #


@dataclass
class WorkerClient:
    model: str = field(
        default_factory=lambda: os.environ.get(
            "LOOPARENA_WORKER_MODEL", DEFAULT_WORKER_MODEL
        )
    )
    api_key_env: str = DEFAULT_API_KEY_ENV
    allow_api_key_fallback: bool = True
    base_url: str = field(default_factory=default_worker_base_url)
    timeout_sec: float = 120
    transport: str = "auto"  # auto | openai | http
    streaming: bool = False
    gateway_attempt_limit: int = WORKER_GATEWAY_ATTEMPT_LIMIT
    credential_profile_id: str = field(
        default_factory=lambda: os.environ.get("LOOPARENA_CREDENTIAL_PROFILE_ID", "")
    )
    context_capacity_utf8_bytes: int = field(
        default_factory=lambda: int(
            os.environ.get(
                "LOOPARENA_CONTEXT_CAPACITY_UTF8_BYTES",
                str(DEFAULT_CONTEXT_CAPACITY_UTF8_BYTES),
            )
        )
    )

    def _credentials(self) -> list[tuple[str, str, str]]:
        env_names = [self.api_key_env]
        if self.allow_api_key_fallback:
            env_names.extend(
                [
                    EXPLICIT_FALLBACK_API_KEY_ENV,
                    "DASHSCOPE_API_KEY",
                    "QWEN_API_KEY",
                ]
            )
        credentials: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for env_name in dict.fromkeys(env_names):
            key = os.environ.get(env_name)
            if not key or key in seen:
                continue
            seen.add(key)
            if env_name == self.api_key_env:
                profile_id = self.credential_profile_id or "primary"
            elif env_name == EXPLICIT_FALLBACK_API_KEY_ENV:
                profile_id = os.environ.get(EXPLICIT_FALLBACK_PROFILE_ENV) or "fallback"
            else:
                profile_id = env_name.lower()
            credentials.append((key, env_name, profile_id))
        return credentials

    def _api_key(self) -> tuple[str, str]:
        credentials = self._credentials()
        if credentials:
            key, env_name, _ = credentials[0]
            return key, env_name
        return "", self.api_key_env

    def _openai_client(self, key: str | None = None):
        if key is None:
            key, env_name = self._api_key()
        else:
            env_name = self.api_key_env
        if not key:
            raise RuntimeError(
                f"missing {env_name} (set OPENAI_API_KEY, DASHSCOPE_API_KEY, "
                "or QWEN_API_KEY in .env or the environment)"
            )
        from openai import OpenAI  # optional dep; installed in this env

        return OpenAI(
            api_key=key,
            base_url=self.base_url,
            timeout=max(0.001, float(self.timeout_sec)),
            max_retries=0,
        )

    def _payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        seed: int | None,
        temperature: float | None,
        max_tokens: int,
        extra_body: dict | None,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if seed is not None:
            payload["seed"] = seed
        if tools:
            payload["tools"] = tools
            # The worker loop executes one stateful repository action at a
            # time. Make that API contract explicit instead of relying only on
            # the prompt or a provider-specific default.
            payload["parallel_tool_calls"] = False
        if extra_body:
            payload.update(extra_body)
        input_bytes = len(
            json.dumps(
                {"messages": messages, "tools": tools or []},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        reserved_output_bytes = max(0, int(max_tokens)) * 4
        if input_bytes + reserved_output_bytes > self.context_capacity_utf8_bytes:
            raise RuntimeError(
                "context_capacity_exhausted:"
                f"input_utf8_bytes={input_bytes}:"
                f"reserved_output_utf8_bytes={reserved_output_bytes}:"
                f"capacity_utf8_bytes={self.context_capacity_utf8_bytes}"
            )
        payload["_looparena_context_audit"] = {
            "input_utf8_bytes": input_bytes,
            "reserved_output_utf8_bytes": reserved_output_bytes,
            "capacity_utf8_bytes": self.context_capacity_utf8_bytes,
            "policy": "full_input_fail_closed_utf8",
        }
        return payload

    def _chat_with_openai_sdk(
        self, payload: dict, *, api_key: str | None = None
    ) -> dict:
        context_audit = payload.pop("_looparena_context_audit", {})
        client = self._openai_client(api_key)
        extra_keys = {"enable_thinking"}
        extra_body = {k: payload.pop(k) for k in list(payload) if k in extra_keys}
        if extra_body:
            payload["extra_body"] = extra_body
        resp = client.chat.completions.create(**payload)
        gateway_error = getattr(resp, "error", None)
        if gateway_error:
            code = str(
                gateway_error.get("code")
                if isinstance(gateway_error, dict)
                else getattr(gateway_error, "code", "unknown")
            )
            message = (
                gateway_error.get("message")
                if isinstance(gateway_error, dict)
                else getattr(gateway_error, "message", gateway_error)
            )
            raise RuntimeError(
                "gateway_configuration_error:"
                f"code={code}:message={_redact(str(message))[:400]}"
            )
        if not getattr(resp, "choices", None):
            raise RuntimeError("gateway_response_invalid:no_choices")
        choice = resp.choices[0]
        msg = choice.message
        normalized = _normalize_message(
            {
                "content": msg.content or "",
                "tool_calls": getattr(msg, "tool_calls", None),
                "reasoning_content": getattr(msg, "reasoning_content", None),
            },
            response_audit=_response_audit(
                transport="openai",
                message=msg,
                finish_reason=getattr(choice, "finish_reason", None),
                usage=getattr(resp, "usage", None),
            ),
        )
        normalized["_looparena_context_audit"] = context_audit
        return normalized

    def _chat_with_http(self, payload: dict, *, api_key: str | None = None) -> dict:
        if self.streaming:
            return self._chat_with_http_stream(payload, api_key=api_key)
        context_audit = payload.pop("_looparena_context_audit", {})
        if api_key is None:
            key, env_name = self._api_key()
        else:
            key, env_name = api_key, self.api_key_env
        if not key:
            raise RuntimeError(
                f"missing {env_name} (set OPENAI_API_KEY, DASHSCOPE_API_KEY, "
                "or QWEN_API_KEY in .env or the environment)"
            )
        url = self.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LLM gateway HTTP {exc.code}: {_redact(body)[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"LLM gateway request failed: {_redact(str(exc))[:300]}"
            ) from exc
        data = json.loads(raw)
        gateway_error = data.get("error")
        if isinstance(gateway_error, dict):
            code = str(gateway_error.get("code") or "unknown")
            message = _redact(str(gateway_error.get("message") or "unknown error"))
            raise RuntimeError(
                f"gateway_configuration_error:code={code}:message={message[:400]}"
            )
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("gateway_response_invalid:no_choices")
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = (
            choice.get("message") if isinstance(choice.get("message"), dict) else {}
        )
        normalized = _normalize_message(
            message,
            response_audit=_response_audit(
                transport="http",
                message=message,
                finish_reason=choice.get("finish_reason"),
                usage=data.get("usage"),
            ),
        )
        normalized["_looparena_context_audit"] = context_audit
        return normalized

    def _chat_with_http_stream(
        self,
        payload: dict,
        *,
        api_key: str | None = None,
    ) -> dict:
        """Read one OpenAI-compatible chat completion as an SSE stream.

        The gateway's public ingress closes connections that are silent for 90
        seconds.  Streaming keeps long tool-call responses active without
        changing the model request or exposing partial output to the harness.
        """

        context_audit = payload.pop("_looparena_context_audit", {})
        if api_key is None:
            key, env_name = self._api_key()
        else:
            key, env_name = api_key, self.api_key_env
        if not key:
            raise RuntimeError(
                f"missing {env_name} (set OPENAI_API_KEY, DASHSCOPE_API_KEY, "
                "or QWEN_API_KEY in .env or the environment)"
            )
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        url = self.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                chunks = _read_chat_completion_stream(resp)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LLM gateway HTTP {exc.code}: {_redact(body)[:500]}"
            ) from exc
        except TimeoutError:
            # Preserve the watchdog's hard deadline. TimeoutError is an OSError
            # subclass and must not be wrapped as a retryable transport error.
            raise
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(
                f"LLM gateway request failed: {_redact(str(exc))[:300]}"
            ) from exc

        message = chunks["message"]
        normalized = _normalize_message(
            message,
            response_audit=_response_audit(
                transport="http-sse",
                message=message,
                finish_reason=chunks["finish_reason"],
                usage=chunks["usage"],
            ),
        )
        normalized["_looparena_context_audit"] = context_audit
        return normalized

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        seed: int | None = 0,
        temperature: float | None = 0.7,
        max_tokens: int = 8192,
        extra_body: dict | None = None,
        deadline_monotonic: float | None = None,
    ) -> dict:
        """One chat turn. Returns the assistant message as a plain dict with
        ``content`` and ``tool_calls`` (OpenAI tool-calling shape)."""
        if provider_family(self.model) in {"gpt", "claude"}:
            # Common GPT and Claude endpoints use provider-default sampling and
            # may reject Qwen-style seed or temperature parameters.
            seed = None
            temperature = None
        payload = self._payload(
            messages,
            tools,
            seed=seed,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        credentials = self._credentials()
        if not credentials:
            raise RuntimeError(
                f"missing {self.api_key_env} (set OPENAI_API_KEY in the environment)"
            )
        total_attempts = 0
        credential_audit: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for api_key, _env_name, profile_id in credentials:
            attempts_for_profile = 0
            profile_status = "transient_failures_exhausted"
            for attempt in range(self.gateway_attempt_limit):
                if (
                    deadline_monotonic is not None
                    and time.monotonic() >= deadline_monotonic
                ):
                    raise TimeoutError(
                        "LLM gateway retry budget exceeded worker deadline"
                    )
                attempts_for_profile += 1
                total_attempts += 1
                try:
                    if self.streaming or self.transport == "http":
                        response = self._chat_with_http(dict(payload), api_key=api_key)
                    elif self.transport == "openai":
                        response = self._chat_with_openai_sdk(
                            dict(payload), api_key=api_key
                        )
                    else:
                        try:
                            response = self._chat_with_openai_sdk(
                                dict(payload), api_key=api_key
                            )
                        except ModuleNotFoundError as exc:
                            if exc.name != "openai":
                                raise
                            response = self._chat_with_http(
                                dict(payload), api_key=api_key
                            )
                    profile_status = "completed"
                    credential_audit.append(
                        {
                            "credential_profile_id": profile_id,
                            "attempts": attempts_for_profile,
                            "status": profile_status,
                        }
                    )
                    response["_looparena_gateway_attempts"] = total_attempts
                    response["_looparena_gateway_credential_attempts"] = (
                        credential_audit
                    )
                    return response
                except Exception as exc:
                    last_error = exc
                    if _gateway_error_is_credential_specific(exc):
                        profile_status = "credential_rejected"
                        break
                    if str(exc).startswith(
                        "missing "
                    ) or _gateway_error_is_non_retryable(exc):
                        profile_status = "request_rejected"
                        credential_audit.append(
                            {
                                "credential_profile_id": profile_id,
                                "attempts": attempts_for_profile,
                                "status": profile_status,
                            }
                        )
                        error = RuntimeError(
                            "gateway_request_rejected:" + _redact(str(exc))[:500]
                        )
                        error.error_code = "gateway_request_rejected"  # type: ignore[attr-defined]
                        error.gateway_credential_attempts = [  # type: ignore[attr-defined]
                            dict(row) for row in credential_audit
                        ]
                        raise error from exc
                    if (
                        deadline_monotonic is not None
                        and time.monotonic() >= deadline_monotonic
                    ):
                        raise TimeoutError(
                            "LLM gateway retry budget exceeded worker deadline"
                        ) from exc
                    if attempt == self.gateway_attempt_limit - 1:
                        break
                    delay = 3 * (attempt + 1)
                    if deadline_monotonic is not None:
                        remaining = deadline_monotonic - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                "LLM gateway retry budget exceeded worker deadline"
                            ) from exc
                        delay = min(delay, remaining)
                    time.sleep(delay)
            credential_audit.append(
                {
                    "credential_profile_id": profile_id,
                    "attempts": attempts_for_profile,
                    "status": profile_status,
                }
            )
        error = RuntimeError(
            "LLM gateway call failed after authorized credential retries: "
            + _redact(str(last_error or "unknown provider error"))[:400]
        )
        error.error_code = "gateway_retries_exhausted"  # type: ignore[attr-defined]
        error.gateway_credential_attempts = [  # type: ignore[attr-defined]
            dict(row) for row in credential_audit
        ]
        raise error from last_error


def _response_audit(
    *,
    transport: str,
    message: Any,
    finish_reason: Any = None,
    usage: Any = None,
) -> dict[str, Any]:
    """Keep the response metadata needed for cost and runtime analysis."""

    if isinstance(message, dict):
        tool_calls = message.get("tool_calls") or []
    else:
        tool_calls = getattr(message, "tool_calls", None) or []

    def usage_value(name: str) -> int | None:
        value = (
            usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        )
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )

    def nested_usage_value(parent: str, name: str) -> int | None:
        details = (
            usage.get(parent)
            if isinstance(usage, dict)
            else getattr(usage, parent, None)
        )
        value = (
            details.get(name)
            if isinstance(details, dict)
            else getattr(details, name, None)
        )
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )

    prompt_tokens = usage_value("prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage_value("input_tokens")
    completion_tokens = usage_value("completion_tokens")
    if completion_tokens is None:
        completion_tokens = usage_value("output_tokens")
    cached_prompt_tokens = nested_usage_value("prompt_tokens_details", "cached_tokens")
    if cached_prompt_tokens is None:
        cached_prompt_tokens = nested_usage_value(
            "input_tokens_details", "cached_tokens"
        )

    return {
        "transport": str(transport),
        "finish_reason": str(finish_reason or ""),
        "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": usage_value("total_tokens"),
            "cached_prompt_tokens": cached_prompt_tokens,
            "cache_creation_input_tokens": usage_value("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage_value("cache_read_input_tokens"),
        },
    }


def _normalize_message(
    message: Any, *, response_audit: dict[str, Any] | None = None
) -> dict:
    if not isinstance(message, dict):
        message = {
            "content": getattr(message, "content", "") or "",
            "tool_calls": getattr(message, "tool_calls", None),
        }
    out: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        normalized = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                normalized.append(tc)
            else:
                normalized.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                )
        out["tool_calls"] = normalized
    if response_audit is not None:
        out["_looparena_response_audit"] = response_audit
    return out
