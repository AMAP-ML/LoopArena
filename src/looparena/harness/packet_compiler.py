"""Build the Controller packet from a validated Reporter result."""

from __future__ import annotations

import copy
import re
from typing import Any

from . import protocol as P
from . import validation as V
from .transcript import worker_evidence_ref

_BRACKETED_TEXT_RE = re.compile(r"\[([^\]\r\n]*)\]")
_EVIDENCE_CITATION_ITEM = r"E[1-9]\d*(?:\s*-\s*E[1-9]\d*)?"
_EVIDENCE_CITATION_RE = re.compile(
    rf"{_EVIDENCE_CITATION_ITEM}(?:\s*,\s*{_EVIDENCE_CITATION_ITEM})*"
)
_EVIDENCE_RANGE_RE = re.compile(r"E([1-9]\d*)\s*-\s*E([1-9]\d*)")


def _citation_refs(
    value: str,
    allowed_refs: set[str],
) -> tuple[list[str], list[str]]:
    refs: list[str] = []
    errors: list[str] = []
    for match in _BRACKETED_TEXT_RE.finditer(value):
        content = match.group(1)
        if not _EVIDENCE_CITATION_RE.fullmatch(content):
            continue
        for item in re.split(r"\s*,\s*", content):
            range_match = _EVIDENCE_RANGE_RE.fullmatch(item)
            if range_match is None:
                ref = item.strip()
                if ref in allowed_refs:
                    refs.append(ref)
                else:
                    errors.append(f"round_report_evidence_ref_unknown:{ref}")
                continue

            start = int(range_match.group(1))
            end = int(range_match.group(2))
            range_label = f"E{start}-E{end}"
            if end < start:
                errors.append(f"round_report_evidence_range_reversed:{range_label}")
                continue

            # Avoid constructing an arbitrary model-authored numeric range.
            # A valid continuous range cannot contain more labels than the
            # complete set of labels that the Reporter was actually shown.
            if end - start + 1 > len(allowed_refs):
                errors.append(f"round_report_evidence_range_unknown:{range_label}")
                continue

            expanded = [f"E{number}" for number in range(start, end + 1)]
            missing = [ref for ref in expanded if ref not in allowed_refs]
            if missing:
                errors.extend(
                    f"round_report_evidence_ref_unknown:{ref}" for ref in missing
                )
                continue
            refs.extend(expanded)
    return refs, errors


def report_evidence_refs(report: object, allowed_refs: set[str]) -> list[str]:
    """Return inline evidence references in first-appearance order."""

    if not isinstance(report, dict):
        return []
    refs: list[str] = []
    seen: set[str] = set()
    for value in report.values():
        if not isinstance(value, str):
            continue
        cited, _errors = _citation_refs(value, allowed_refs)
        for ref in cited:
            if ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    return refs


def validate_report_evidence_refs(
    report: object,
    allowed_refs: set[str],
) -> list[str]:
    """Require usable citations and reject malformed or unknown references.

    Ordinary Markdown bracket text is not evidence syntax. Only a complete
    ``[E12]`` or ``[E12, E13]`` group is interpreted as a citation; the parser
    deliberately does not guess whether labels such as ``[TYPE2]`` were
    intended as evidence. A standalone ``E<n>`` token outside a valid citation
    remains ordinary prose and does not select evidence for the Controller.
    """

    if not isinstance(report, dict):
        return []
    errors: list[str] = []
    cited_refs = report_evidence_refs(report, allowed_refs)
    for value in report.values():
        if not isinstance(value, str):
            continue
        _refs, citation_errors = _citation_refs(value, allowed_refs)
        errors.extend(citation_errors)
    if allowed_refs and not cited_refs and not errors:
        errors.append(
            "round_report_evidence_ref_required:"
            "use_[E12]_[E12,_E13]_or_[E12-E15]_syntax"
        )
    return list(dict.fromkeys(errors))


def _worker_turn_lookup(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for event in events:
        ref = worker_evidence_ref(event)
        if not ref:
            continue
        if ref in lookup:
            raise ValueError(f"duplicate_worker_turn_evidence_ref:{ref}")
        lookup[ref] = event
    return lookup


def _selected_worker_evidence(
    report: dict[str, Any],
    worker_turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select cited coding-agent turns without changing their contents."""

    worker_lookup = _worker_turn_lookup(worker_turns)
    requested = set(report_evidence_refs(report, set(worker_lookup)))
    missing = sorted(requested - set(worker_lookup))
    if missing:
        raise ValueError("unresolvable_evidence_refs:" + ",".join(missing))

    return [
        copy.deepcopy(turn)
        for turn in worker_turns
        if worker_evidence_ref(turn) in requested
    ]


def compile_packet_from_reporter(
    previous_packet: dict[str, Any],
    *,
    reporter_report: dict[str, Any],
    worker_evidence_turns: list[dict[str, Any]],
    main_worker_turns_used: int,
    cycle_index: int,
    active_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap one validated report without re-summarizing or accumulating it."""

    if not isinstance(reporter_report, dict):
        raise ValueError("reporter_report must be an object")
    active_contract = copy.deepcopy(active_contract or {})
    quoted_worker_evidence = _selected_worker_evidence(
        reporter_report,
        worker_evidence_turns,
    )
    total = int(
        (previous_packet.get("budget") or {}).get("max_inner_react_turns_total") or 600
    )
    used = max(0, min(total, int(main_worker_turns_used)))
    packet = {
        "sample_id": previous_packet.get("sample_id"),
        "task": previous_packet.get("task"),
        "report_context": {
            "kind": (
                "initial_orientation"
                if not active_contract or cycle_index == 1
                else "controller_followup"
            ),
            "previous_action": str(
                (active_contract.get("control_decision") or {}).get("action") or ""
            ),
        },
        "round_report": copy.deepcopy(reporter_report),
        "quoted_worker_evidence": quoted_worker_evidence,
        "budget": {
            "budget_unit": "main_worker_react_turn",
            "max_inner_react_turns_total": total,
            "used_inner_react_turns": used,
            "remaining_inner_react_turns": total - used,
        },
        "allowed_actions": list(P.CONTROL_DECISIONS),
        "round_index": cycle_index,
    }
    errors = V.validate_packet(packet)
    if errors:
        raise ValueError("compiled packet failed validation: " + ",".join(errors))
    return packet
