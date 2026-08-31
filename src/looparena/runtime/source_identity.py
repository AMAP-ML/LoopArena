"""Small, human-readable source revision record for harness runs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from looparena import __version__


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def capture_harness_identity(
    repo_root: Path,
) -> dict[str, Any]:
    """Record the Git revision and whether relevant local files are dirty."""

    root = repo_root.resolve()
    try:
        head = _run_git(root, ["rev-parse", "HEAD"]).strip().lower()
    except RuntimeError:
        return {
            "harness_git_head": None,
            "harness_worktree_dirty": None,
            "harness_release_version": __version__,
        }
    scope = ["src/looparena"]
    tracked_diff = _run_git(root, ["diff", "--binary", "HEAD", "--", *scope])
    untracked = _run_git(
        root,
        ["ls-files", "--others", "--exclude-standard", "--", *scope],
    )
    return {
        "harness_git_head": head,
        "harness_worktree_dirty": bool(tracked_diff or untracked.strip()),
        "harness_release_version": __version__,
    }
