"""Durable state for interrupted natural-inner-loop runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Replace one JSON object durably, including its directory entry."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_checkpoint(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("recovery checkpoint must contain an object")
    return value


__all__ = [
    "atomic_write_json",
    "read_checkpoint",
]
