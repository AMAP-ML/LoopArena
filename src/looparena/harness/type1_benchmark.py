"""Small shared helpers for the Type-I benchmark."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from .rendering import controller_decision_policy_prompt, render_controller_packet

TYPE1_SYSTEM_PROMPT = (
    controller_decision_policy_prompt()
    + """

## Your task

You are evaluating one decision point from an ongoing coding-agent run. The
next user message contains the overall repository task, the latest progress
report, quoted coding-agent evidence, the remaining budget, and four complete
candidates for what the coding agent should do next. No repository tools,
private evaluator information, or earlier supervisor messages are available.

Apply the supervision rules above to the supplied information. Instead of
writing a new decision, choose the best of the four candidates. Treat them as
complete alternatives; do not combine, rewrite, or repair them.
"""
)


_EXPLICIT_ANSWER_RE = re.compile(
    r"^\s*\*{0,2}(?:answer|choice)\s*[:=]\s*['\"]?([ABCD])['\"]?"
    r"\s*[.]?\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_BARE_ANSWER_RE = re.compile(r"^\s*([ABCD])\s*[.]?\s*$", re.IGNORECASE)


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _replace_report_context(rendered: str, packet: dict) -> str:
    context = packet.get("report_context")
    context = context if isinstance(context, dict) else {}
    if context.get("kind") == "initial_orientation":
        return rendered
    previous = context.get("previous_action")
    previous_line = (
        f"The source run's preceding control decision was `{previous}`."
        if previous in {"advance", "verify", "stop"}
        else "The source run occurred after at least one earlier control decision."
    )
    replacement = "\n".join(
        [
            "<report_context>",
            "This is a standalone view of a later Controller decision point.",
            previous_line,
            "The report below describes the complete recorded work and current",
            "repository state at this point. No earlier Controller messages are",
            "included; use the latest report and quoted evidence below.",
            "</report_context>",
        ]
    )
    return re.sub(
        r"<report_context>\n.*?\n</report_context>",
        replacement,
        rendered,
        count=1,
        flags=re.DOTALL,
    )


def _render_harness_context(public_item: dict) -> str:
    packet = dict(public_item["public_controller_packet"])
    packet["task"] = public_item["task"]
    rendered = render_controller_packet(packet, first_turn=True)
    rendered = _replace_report_context(rendered, packet)
    context, separator, _ = rendered.partition("\n## Decision requested\n")
    if not separator:
        raise RuntimeError("Rendered Controller packet has no decision section")
    return context


def format_question(public_item: dict) -> str:
    """Adapt the harness Controller prompt into one multiple-choice question."""

    sections = [_render_harness_context(public_item), "# Candidate control decisions"]
    for letter in "ABCD":
        sections.append(f"## {letter}\n\n{_json_block(public_item['options'][letter])}")
    sections.append(
        """Which candidate is the best next control decision?

You may explain your reasoning. End your response with a separate line in this
form, where X is A, B, C, or D:

Answer: X"""
    )
    return "\n\n".join(section for section in sections if section.strip())


def make_input(public_item: dict) -> list[dict[str, str]]:
    """Return the harness-aligned prompt used for one Type-I question."""

    return [
        {"role": "system", "content": TYPE1_SYSTEM_PROMPT},
        {"role": "user", "content": format_question(public_item)},
    ]


def parse_choice(text: str) -> str | None:
    """Extract one A-D answer without treating mentions inside rationale as votes."""

    stripped = text.strip()
    candidates = [stripped]
    if "```" in stripped:
        candidates.extend(
            part.strip() for part in stripped.split("```") if part.strip()
        )
    for candidate in candidates:
        candidate = candidate.removeprefix("json").strip()
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            choice = value.get("choice", value.get("answer"))
            if (
                isinstance(choice, str)
                and choice.upper() in "ABCD"
                and len(choice) == 1
            ):
                return choice.upper()
    explicit = list(_EXPLICIT_ANSWER_RE.finditer(stripped))
    if explicit:
        return explicit[-1].group(1).upper()
    match = _BARE_ANSWER_RE.match(stripped)
    return match.group(1).upper() if match else None


def summarize(predictions: Iterable[dict]) -> dict[str, int | float | bool | None]:
    """Aggregate item-level records into the benchmark's two core metrics."""

    rows = list(predictions)
    correct = sum(row.get("correct") is True for row in rows)
    api_errors = sum(bool(row.get("error")) for row in rows)
    invalid = sum(
        row.get("prediction") is None and not row.get("error") for row in rows
    )
    total = len(rows)
    scored = total - api_errors
    observed_accuracy = correct / scored if scored else 0.0
    complete = api_errors == 0
    return {
        "items": total,
        "scored_items": scored,
        "correct": correct,
        "incorrect": scored - correct - invalid,
        "invalid": invalid,
        "api_errors": api_errors,
        "complete": complete,
        "accuracy": observed_accuracy if complete else None,
        "observed_scored_accuracy": observed_accuracy,
    }
