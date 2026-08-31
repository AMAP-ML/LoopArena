"""OpenAI-compatible Controller client."""

from __future__ import annotations

import time
from typing import Any

from looparena.harness.controller import CONTROLLER_MAX_OUTPUT_TOKENS

from . import llm as _llm


class ControllerCallError(RuntimeError):
    """A Controller failure with a safe, machine-readable classification."""

    def __init__(
        self,
        message: str,
        *,
        failure_kind: str,
        error_code: str,
    ) -> None:
        safe_message = _llm._redact(message)[:500]
        super().__init__(safe_message)
        self.failure_kind = failure_kind
        self.error_code = error_code
        self.safe_message = safe_message


def _normalized_error_code(exc: Exception, default: str) -> str:
    code = getattr(exc, "error_code", None)
    if isinstance(code, str) and code:
        return code
    prefix = str(exc).partition(":")[0]
    if prefix and all(character.isalnum() or character == "_" for character in prefix):
        return prefix
    return default


class ControllerChatClient:
    """Adapt the shared gateway client to the Controller interface.

    This changes only how requests are sent. Worker, Reporter, and Controller
    prompts and harness semantics are unchanged.
    """

    provider_kind = "model"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        temperature: float,
        timeout: int,
        credential_profile_id: str,
        api_key_env: str = _llm.DEFAULT_API_KEY_ENV,
        max_retries: int = 3,
        seed: int = 0,
        transport: str = "auto",
    ) -> None:
        self.model = model
        cache_family = _llm.provider_family(model)
        # Some GPT and Claude endpoints reject explicit sampling parameters.
        # Using the provider default does not change model-visible prompts.
        self.temperature = None if cache_family in {"gpt", "claude"} else temperature
        self.max_retries = max_retries
        self.seed = int(seed)
        self.seed_supported = cache_family not in {"gpt", "claude"}
        self.call_index = 0
        self.deadline_monotonic: float | None = None
        self.transport = transport
        self.base_url = base_url
        self.credential_profile_id = credential_profile_id
        self.timeout_sec = timeout
        self.thinking_mode = "provider_default"
        self.call_audits: list[dict[str, Any]] = []
        self.client = _llm.WorkerClient(
            model=model,
            base_url=base_url,
            timeout_sec=timeout,
            transport=transport,
            streaming=True,
            credential_profile_id=credential_profile_id,
            api_key_env=api_key_env,
        )

    def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> str:
        started = time.monotonic()
        last_error: Exception | None = None
        call_seed = self.seed + self.call_index
        sent_seed = call_seed if self.seed_supported else None
        self.call_index += 1
        for attempt in range(self.max_retries):
            try:
                response = self.client.chat(
                    messages=messages,
                    tools=None,
                    temperature=self.temperature,
                    max_tokens=max_tokens or CONTROLLER_MAX_OUTPUT_TOKENS,
                    seed=sent_seed,
                    deadline_monotonic=self.deadline_monotonic,
                )
                self.call_audits.append(
                    {
                        "seed": sent_seed,
                        "temperature": self.temperature,
                        "max_output_tokens": (
                            max_tokens or CONTROLLER_MAX_OUTPUT_TOKENS
                        ),
                        "thinking_mode": self.thinking_mode,
                        "outer_attempts": attempt + 1,
                        "wall_time_sec": round(time.monotonic() - started, 6),
                        "response_audit": response.get("_looparena_response_audit"),
                        "context_audit": response.get("_looparena_context_audit"),
                        "gateway_attempts": response.get("_looparena_gateway_attempts"),
                        "gateway_credential_attempts": response.get(
                            "_looparena_gateway_credential_attempts"
                        ),
                        "status": "completed",
                    }
                )
                return str(response.get("content") or "")
            except Exception as exc:  # pragma: no cover - real API path
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)

        assert last_error is not None
        failure = ControllerCallError(
            f"controller model call failed: {last_error}",
            failure_kind=str(
                getattr(last_error, "failure_kind", "infrastructure_transport")
            ),
            error_code=_normalized_error_code(last_error, "gateway_controller_failed"),
        )
        self.call_audits.append(
            {
                "provider_sampling_mode": "provider_default",
                "seed": sent_seed,
                "temperature": self.temperature,
                "max_output_tokens": max_tokens or CONTROLLER_MAX_OUTPUT_TOKENS,
                "thinking_mode": self.thinking_mode,
                "outer_attempts": self.max_retries,
                "wall_time_sec": round(time.monotonic() - started, 6),
                "status": "failed",
                "transport": self.transport,
                "failure_kind": failure.failure_kind,
                "error_type": type(last_error).__name__,
                "error_code": failure.error_code,
                "redacted_error": failure.safe_message,
                "gateway_credential_attempts": getattr(
                    last_error, "gateway_credential_attempts", []
                ),
            }
        )
        raise failure from last_error


__all__ = ["ControllerCallError", "ControllerChatClient"]
