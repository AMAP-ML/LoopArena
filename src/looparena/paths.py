"""Paths used by commands running from a LoopArena source checkout."""

from __future__ import annotations

import os
from pathlib import Path


def repository_root() -> Path:
    """Return the checkout containing benchmark data.

    ``LOOPARENA_HOME`` supports an installed CLI used against a separate source
    checkout. Editable installs resolve the checkout directly.
    """

    configured = os.environ.get("LOOPARENA_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    current = Path.cwd().resolve()
    if (current / "benchmarks").is_dir():
        return current
    return Path(__file__).resolve().parents[2]
