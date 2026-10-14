"""Type III package constants and small manifest helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any



def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def safe_relative(root: Path, raw: object, field: str) -> Path:
    relative = Path(str(raw or ""))
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe relative path for {field}: {raw!r}")
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
