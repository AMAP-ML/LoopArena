#!/usr/bin/env python3
"""Run a Type-I benchmark file through an OpenAI-compatible model API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from looparena.harness.type1_benchmark import parse_choice, summarize
from looparena.runtime.llm import (
    DEFAULT_API_KEY_ENV,
    WorkerClient,
    _redact,
    default_worker_base_url,
    provider_family,
)

DEFAULT_MAX_TOKENS = 20480


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _validate_questions(questions: list[dict]) -> None:
    if not questions:
        raise ValueError("Type I data contains no questions")
    seen: set[str] = set()
    for index, question in enumerate(questions, start=1):
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id.strip():
            raise ValueError(f"question {index} has no id")
        if question_id in seen:
            raise ValueError(f"duplicate question id: {question_id}")
        seen.add(question_id)
        messages = question.get("input")
        if not isinstance(messages, list) or not messages:
            raise ValueError(f"{question_id} has no input messages")
        if question.get("ideal") not in {"A", "B", "C", "D"}:
            raise ValueError(f"{question_id} has an invalid ideal choice")


def _evaluate_one(client: WorkerClient, max_tokens: int, question: dict) -> dict:
    try:
        message = client.chat(
            question["input"],
            tools=None,
            seed=None,
            temperature=0,
            max_tokens=max_tokens,
        )
        text = str(message.get("content") or "")
        prediction = parse_choice(text)
        return {
            "id": question["id"],
            "response": text,
            "prediction": prediction,
            "ideal": question["ideal"],
            "correct": prediction == question["ideal"],
            "error": None,
            "response_audit": message.get("_looparena_response_audit"),
        }
    except Exception as exc:  # Preserve other completed items in a batch.
        return {
            "id": question["id"],
            "response": "",
            "prediction": None,
            "ideal": question["ideal"],
            "correct": False,
            "error": _redact(f"{type(exc).__name__}: {exc}"),
            "response_audit": None,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--base-url", default=default_worker_base_url())
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate the data and model configuration without making a model call.",
    )
    args = parser.parse_args(argv)

    if not os.environ.get(args.api_key_env):
        parser.error(f"missing API key in {args.api_key_env}")
    if not args.preflight_only and args.output is None:
        parser.error("--output is required unless --preflight-only is used")
    if args.max_tokens <= 0 or args.concurrency <= 0 or args.timeout <= 0:
        parser.error("--max-tokens, --concurrency, and --timeout must be positive")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    try:
        questions = _load_jsonl(args.data)
        _validate_questions(questions)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"invalid Type I data: {exc}")
    if args.limit is not None:
        questions = questions[: args.limit]
    if args.preflight_only:
        print(
            json.dumps(
                {
                    "ready": True,
                    "data": str(args.data.expanduser().resolve()),
                    "data_sha256": hashlib.sha256(args.data.read_bytes()).hexdigest(),
                    "questions": len(questions),
                    "model": args.model,
                    "base_url": "[REDACTED]",
                    "api_key_env": args.api_key_env,
                    "model_calls": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    client = WorkerClient(
        model=args.model,
        api_key_env=args.api_key_env,
        allow_api_key_fallback=False,
        base_url=args.base_url,
        timeout_sec=args.timeout,
        gateway_attempt_limit=1,
    )
    records: list[dict | None] = [None] * len(questions)
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as executor:
        futures = {
            executor.submit(_evaluate_one, client, args.max_tokens, question): index
            for index, question in enumerate(questions)
        }
        for future in as_completed(futures):
            records[futures[future]] = future.result()

    completed = [record for record in records if record is not None]
    assert args.output is not None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in completed:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = summarize(completed)
    summary.update(
        {
            "provider": "openai-compatible",
            "model": args.model,
            "temperature": None if provider_family(args.model) else 0,
        }
    )
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
