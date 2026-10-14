"""Answer parsing and scoring for Type I."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

_EXPLICIT_ANSWER_RE = re.compile(
    r"^\s*\*{0,2}(?:answer|choice)\s*[:=]\s*['\"]?([ABCD])['\"]?"
    r"\s*[.]?\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_BARE_ANSWER_RE = re.compile(r"^\s*([ABCD])\s*[.]?\s*$", re.IGNORECASE)

def parse_choice(text: str) -> str | None:
    """Extract one A-D answer without treating mentions inside rationale as votes."""

    stripped = text.strip()
    candidates = [stripped]
    if "```" in stripped:
        candidates.extend(part.strip() for part in stripped.split("```") if part.strip())
    for candidate in candidates:
        candidate = candidate.removeprefix("json").strip()
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            choice = value.get("choice", value.get("answer"))
            if isinstance(choice, str) and choice.upper() in "ABCD" and len(choice) == 1:
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
