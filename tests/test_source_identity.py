from __future__ import annotations

from pathlib import Path

from looparena import __version__
from looparena.runtime.source_identity import capture_harness_identity


def test_source_archive_uses_release_version_without_git(tmp_path: Path) -> None:
    (tmp_path / "src/looparena").mkdir(parents=True)
    identity = capture_harness_identity(tmp_path)
    assert identity == {
        "harness_git_head": None,
        "harness_worktree_dirty": None,
        "harness_release_version": __version__,
    }
