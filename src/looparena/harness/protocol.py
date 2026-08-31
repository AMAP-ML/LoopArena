"""Constants and small helpers for the current LoopArena harness protocol."""

from __future__ import annotations

CONTROL_DECISIONS = ("advance", "verify", "stop")


def card_label_sets(packet: dict) -> dict[str, set[str]]:
    """Return the model-visible fact and scope labels in a Core packet."""
    return {
        "fact": {
            str(card.get("label"))
            for card in packet.get("fact_cards") or []
            if isinstance(card, dict) and card.get("label")
        },
        "scope": {
            str(card.get("label"))
            for card in packet.get("scope_cards") or []
            if isinstance(card, dict) and card.get("label")
        },
    }


def card_lookup(packet: dict) -> dict[str, dict]:
    """Return a flat label -> card mapping for renderer expansion."""
    out: dict[str, dict] = {}
    for key in ("fact_cards", "scope_cards"):
        for card in packet.get(key) or []:
            if not isinstance(card, dict):
                continue
            label = str(card.get("label") or "")
            if label:
                out[label] = card
    return out


def as_str_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]
