#!/usr/bin/env python3
"""Workspace restoration and Docker execution for Type II/III runtimes."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


class RestoreFidelityError(RuntimeError):
    """A restore op could not be applied faithfully (fail closed, do not skip)."""


MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
COMMAND_OUTPUT_HEAD_BYTES = 8 * 1024
COMMAND_OUTPUT_TAIL_BYTES = 23 * 1024
COMMAND_OUTPUT_INLINE_BYTES = 32 * 1024
ATTEMPT_OWNER_ENV = "LOOPARENA_ATTEMPT_OWNER_ID"
ATTEMPT_OWNER_LABEL = "io.looparena.attempt_owner"


def _run_with_bounded_output(
    argv: list[str],
    *,
    timeout_sec: float,
) -> tuple[int, str]:
    """Drain a subprocess without retaining unbounded output in host memory."""

    process = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if process.stdout is None:  # pragma: no cover - guaranteed by stdout=PIPE
        process.kill()
        raise RuntimeError("failed to open command output pipe")

    full = bytearray()
    head = bytearray()
    tail = bytearray()
    total_bytes = 0
    truncated = False

    def drain() -> None:
        nonlocal total_bytes, truncated
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                return
            total_bytes += len(chunk)
            if not truncated:
                full.extend(chunk)
                if len(full) <= COMMAND_OUTPUT_INLINE_BYTES:
                    continue
                truncated = True
                head.extend(full[:COMMAND_OUTPUT_HEAD_BYTES])
                tail.extend(full[-COMMAND_OUTPUT_TAIL_BYTES:])
                full.clear()
                continue
            tail.extend(chunk)
            if len(tail) > COMMAND_OUTPUT_TAIL_BYTES:
                del tail[:-COMMAND_OUTPUT_TAIL_BYTES]

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        return_code = process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        reader.join()
        partial = bytes(full if not truncated else head + tail)
        raise subprocess.TimeoutExpired(
            argv,
            timeout_sec,
            output=partial.decode("utf-8", errors="replace"),
        )
    reader.join()

    if not truncated:
        return return_code, full.decode("utf-8", errors="replace")

    marker = (
        "\n[output truncated by harness: "
        f"utf8_bytes={total_bytes}; showing first "
        f"{COMMAND_OUTPUT_HEAD_BYTES} and last "
        f"{COMMAND_OUTPUT_TAIL_BYTES} bytes. The command ran in full. "
        "If the omitted section is needed, run a more targeted command that "
        "produces only the relevant output.]\n"
    ).encode("utf-8")
    output = bytes(head) + marker + bytes(tail)
    return return_code, output.decode("utf-8", errors="replace")


def _validate_tar_member(workdir: Path, member: tarfile.TarInfo) -> None:
    root = workdir.resolve()
    target = (root / member.name).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RestoreFidelityError(
            f"snapshot member escapes workspace: {member.name}"
        ) from exc
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        raise RestoreFidelityError(
            f"snapshot member has unsupported file type: {member.name}"
        )
    if member.islnk():
        # tar hard-link targets use archive-root semantics, unlike symlink
        # targets. Reject them instead of relying on Python-version-specific
        # extraction behavior that may resolve them outside the workspace.
        raise RestoreFidelityError(
            f"snapshot member is an unsupported hard link: "
            f"{member.name} -> {member.linkname}"
        )
    if member.issym():
        link = Path(member.linkname)
        if link.is_absolute():
            raise RestoreFidelityError(
                f"snapshot link escapes workspace: {member.name} -> {member.linkname}"
            )
        link_target = (target.parent / link).resolve(strict=False)
        try:
            link_target.relative_to(root)
        except ValueError as exc:
            raise RestoreFidelityError(
                f"snapshot link escapes workspace: {member.name} -> {member.linkname}"
            ) from exc


def safe_extract_workspace_archive(archive: Path, workdir: Path) -> None:
    """Extract a workspace tar safely on every supported Python version."""

    workdir.mkdir(parents=True, exist_ok=True)
    if any(workdir.iterdir()):
        raise RestoreFidelityError("workspace restore destination must be empty")
    try:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise RestoreFidelityError("workspace archive has too many members")
            expanded_bytes = sum(member.size for member in members if member.isfile())
            if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                raise RestoreFidelityError(
                    "workspace archive exceeds expanded-size limit"
                )
            normalized_names = [str(Path(member.name)) for member in members]
            if len(normalized_names) != len(set(normalized_names)):
                raise RestoreFidelityError(
                    "workspace archive contains duplicate member paths"
                )
            for member in members:
                _validate_tar_member(workdir, member)
            try:
                tf.extractall(path=workdir, members=members, filter="data")
            except TypeError as exc:
                # Python <3.12 and a few compatible tarfile shims do not expose
                # the filter parameter. The checks above enforce the same
                # workspace boundary before this compatibility fallback.
                if "filter" not in str(exc):
                    raise
                tf.extractall(path=workdir, members=members)
    except tarfile.TarError as exc:
        raise RestoreFidelityError(f"workspace extract failed: {exc}") from exc


def _remove_archive_transport_metadata(workspace: Path) -> None:
    """Remove host-specific archive entries that are not repository content."""

    for metadata in sorted(
        (
            path
            for path in workspace.rglob("*")
            if path.name == "__MACOSX"
            or path.name == ".DS_Store"
            or path.name.startswith("._")
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        if metadata.is_dir() and not metadata.is_symlink():
            shutil.rmtree(metadata)
        else:
            metadata.unlink()


def normalize_workspace_archive_root(workspace: Path) -> None:
    """Expose a known single workspace wrapper as the solve root.

    Some exported cutpoints contain ``workspace/`` or
    ``controlled_workspace/`` as the only meaningful top-level entry. The
    coding agent and evaluator both need the contents of that wrapper at the
    mounted project root. Archives that contain any other top-level content
    are left unchanged.
    """

    _remove_archive_transport_metadata(workspace)
    entries = list(workspace.iterdir())
    if len(entries) != 1:
        return
    wrapper = entries[0]
    if (
        wrapper.name not in {"workspace", "controlled_workspace"}
        or wrapper.is_symlink()
        or not wrapper.is_dir()
    ):
        return
    for child in wrapper.iterdir():
        child.rename(workspace / child.name)
    wrapper.rmdir()


@dataclass(frozen=True)
class _WorkspaceGitRepository:
    worktree: Path
    git_dir: Path


def _resolve_workspace_git_dir(
    git_marker: Path,
    *,
    workspace_root: Path,
) -> Path:
    """Resolve a directory-style ``.git`` or a safe in-workspace Gitfile.

    Initialized Git submodules normally use a text ``.git`` file whose
    ``gitdir:`` target lives under the superproject's ``.git/modules`` tree.
    Official-start benchmark images may legitimately contain that layout, so
    rejecting every non-directory ``.git`` would reject a faithful source
    image.  We still fail closed when the marker is a symlink, malformed, or
    resolves outside the submitted workspace.
    """

    if git_marker.is_symlink():
        raise RestoreFidelityError("unsupported workspace Git indirection")
    if git_marker.is_dir():
        git_dir = git_marker.resolve()
    elif git_marker.is_file():
        try:
            lines = git_marker.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise RestoreFidelityError("invalid workspace Gitfile") from exc
        if len(lines) != 1 or not lines[0].startswith("gitdir: "):
            raise RestoreFidelityError("invalid workspace Gitfile")
        raw_target = lines[0][len("gitdir: ") :].strip()
        if not raw_target or "\x00" in raw_target:
            raise RestoreFidelityError("invalid workspace Gitfile")
        target = Path(raw_target)
        if not target.is_absolute():
            target = git_marker.parent / target
        git_dir = target.resolve()
    else:
        raise RestoreFidelityError("unsupported workspace Git indirection")

    try:
        git_dir.relative_to(workspace_root)
    except ValueError as exc:
        raise RestoreFidelityError(
            "workspace Git directory escapes the workspace"
        ) from exc
    if not git_dir.is_dir():
        raise RestoreFidelityError("workspace Git directory is unavailable")

    # Let Git validate the Gitfile syntax too, but only after the preliminary
    # path check above has established that it cannot point outside workspace.
    resolved = subprocess.run(
        ["git", "rev-parse", "--resolve-git-dir", str(git_marker)],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if resolved.returncode != 0:
        raise RestoreFidelityError("invalid workspace Git directory")
    observed = Path(resolved.stdout.strip())
    if not observed.is_absolute():
        observed = workspace_root / observed
    if observed.resolve() != git_dir:
        raise RestoreFidelityError("workspace Gitfile resolution mismatch")
    return git_dir


def _workspace_git_repositories(
    workspace_root: Path,
) -> list[_WorkspaceGitRepository]:
    """Discover worktrees before mutating any shared superproject Git state."""

    repositories: dict[tuple[Path, Path], _WorkspaceGitRepository] = {}
    for git_marker in sorted(workspace_root.rglob(".git")):
        worktree = git_marker.parent.resolve()
        try:
            worktree.relative_to(workspace_root)
        except ValueError as exc:
            raise RestoreFidelityError(
                "workspace Git worktree escapes the workspace"
            ) from exc
        git_dir = _resolve_workspace_git_dir(
            git_marker,
            workspace_root=workspace_root,
        )
        observed = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            observed.returncode != 0
            or Path(observed.stdout.strip()).resolve() != git_dir
        ):
            raise RestoreFidelityError("workspace Git worktree mismatch")
        repositories[(worktree, git_dir)] = _WorkspaceGitRepository(
            worktree=worktree,
            git_dir=git_dir,
        )

    # A submodule's real Git directory is stored inside the superproject's
    # .git tree.  Sanitize the deepest worktree first so cleaning the parent
    # cannot invalidate discovery or leave child refs/remotes behind.
    return sorted(
        repositories.values(),
        key=lambda repository: (
            len(repository.worktree.parts),
            str(repository.worktree),
        ),
        reverse=True,
    )


def sanitize_workspace_git_history(workspace: Path) -> None:
    """Keep workspace code state without transport junk or later Git history."""

    workspace_root = workspace.resolve()
    _remove_archive_transport_metadata(workspace_root)

    for discovered in _workspace_git_repositories(workspace_root):
        repository = discovered.worktree
        git_dir = discovered.git_dir

        for metadata in sorted(
            git_dir.rglob("._*"),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            if metadata.is_dir() and not metadata.is_symlink():
                shutil.rmtree(metadata)
            else:
                metadata.unlink()

        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repository), "checkout", "--detach", head],
            capture_output=True,
            text=True,
            check=True,
        )
        remotes = subprocess.run(
            ["git", "-C", str(repository), "remote"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        for remote in remotes:
            subprocess.run(
                ["git", "-C", str(repository), "remote", "remove", remote],
                capture_output=True,
                text=True,
                check=True,
            )
        refs = subprocess.run(
            ["git", "-C", str(repository), "for-each-ref", "--format=%(refname)"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        for ref in refs:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "update-ref",
                    "--no-deref",
                    "-d",
                    ref,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        for pseudo_ref in (
            "ORIG_HEAD",
            "FETCH_HEAD",
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "REBASE_HEAD",
        ):
            (git_dir / pseudo_ref).unlink(missing_ok=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "reflog",
                "expire",
                "--expire=now",
                "--expire-unreachable=now",
                "--all",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "gc", "--prune=now"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Delete empty reflogs and dangling symbolic refs left by Git. Keep
        # empty refs directories so the detached repository remains usable.
        for disposable in (git_dir / "refs", git_dir / "logs"):
            if disposable.exists():
                shutil.rmtree(disposable)
        (git_dir / "packed-refs").unlink(missing_ok=True)
        (git_dir / "refs" / "heads").mkdir(parents=True)
        (git_dir / "refs" / "tags").mkdir(parents=True)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    rc: int
    output: str


@dataclass
class Sandbox:
    """A throwaway Docker container with the workspace mounted at /work.

    ``network`` is a required runtime input. The generic harness CLI owns its
    user-facing ``bridge`` default; lower-level callers must deliberately pass
    the source task's declared policy.
    """

    workdir: Path
    network: str
    backend: str = "docker"
    image: str = "python:3.12-slim"
    mount_point: str = (
        "/work"  # where workdir is mounted (repo-from-doc: /workspace/<repo>)
    )
    exec_timeout: int = 120
    cpus: int | float | None = None
    memory_mb: int | None = None
    workspace_read_only: bool = False
    runtime_dir: Path | None = None
    runtime_read_only: bool = False
    runtime_mount_point: str = "/opt/looparena-runtime"
    runtime_venv_relative_path: str | None = None
    container_id: str | None = field(default=None, init=False)
    image_identity: dict = field(default_factory=dict, init=False)
    container_security_audit: dict = field(default_factory=dict, init=False)
    command_deadline_monotonic: float | None = field(default=None, init=False)

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        if self.backend != "docker":
            raise RuntimeError(f"unsupported sandbox backend: {self.backend}")
        self.workdir.mkdir(parents=True, exist_ok=True)
        if self.runtime_dir is not None:
            self.runtime_dir = self.runtime_dir.resolve()
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._capture_image_identity()
        command = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--network",
            self.network,
        ]
        attempt_owner_id = os.environ.get(ATTEMPT_OWNER_ENV, "").strip()
        if attempt_owner_id is not None:
            command.extend(["--label", f"{ATTEMPT_OWNER_LABEL}={attempt_owner_id}"])
        if self.memory_mb is not None:
            command.extend(["--memory", f"{self.memory_mb}m"])
        if self.cpus is not None:
            command.extend(["--cpus", str(self.cpus)])
        command.extend(
            [
                "-v",
                (
                    f"{self.workdir.resolve()}:{self.mount_point}:ro"
                    if self.workspace_read_only
                    else f"{self.workdir.resolve()}:{self.mount_point}"
                ),
            ]
        )
        if self.runtime_dir is not None:
            command.extend(
                [
                    "-v",
                    (
                        f"{self.runtime_dir}:{self.runtime_mount_point}:ro"
                        if self.runtime_read_only
                        else f"{self.runtime_dir}:{self.runtime_mount_point}"
                    ),
                ]
            )
        command.extend(["-w", self.mount_point, self.image, "sleep", "infinity"])
        out = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if out.returncode != 0:
            raise RuntimeError(f"docker run failed: {out.stderr.strip()[:300]}")
        self.container_id = out.stdout.strip()
        self._audit_container_security()

    def _capture_image_identity(self) -> None:
        result = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker image inspect failed: {result.stderr.strip()[:300]}"
            )
        try:
            rows = json.loads(result.stdout)
            row = rows[0]
        except (json.JSONDecodeError, IndexError, TypeError, KeyError) as exc:
            raise RuntimeError("docker image inspect returned invalid JSON") from exc
        image_id = str(row.get("Id") or "")
        if not image_id:
            raise RuntimeError("docker image inspect did not return an image id")
        self.image_identity = {
            "requested_reference": self.image,
            "image_id": image_id,
            "repo_digests": sorted(
                str(value) for value in row.get("RepoDigests") or []
            ),
        }

    def _audit_container_security(self) -> None:
        if not self.container_id:
            raise RuntimeError("sandbox not started")
        result = subprocess.run(
            ["docker", "inspect", self.container_id],
            capture_output=True,
            text=True,
            timeout=120,
        )
        try:
            rows = json.loads(result.stdout) if result.returncode == 0 else []
            row = rows[0]
        except (json.JSONDecodeError, IndexError, TypeError, KeyError) as exc:
            self.stop()
            raise RuntimeError(
                "docker container inspect returned invalid JSON"
            ) from exc
        host = row.get("HostConfig") or {}
        config = row.get("Config") or {}
        mounts = row.get("Mounts") or []
        expected_source = str(self.workdir.resolve())
        expected_rw = not self.workspace_read_only
        expected_runtime_rw = not self.runtime_read_only
        runtime_source = (
            str(self.runtime_dir.resolve()) if self.runtime_dir is not None else None
        )
        workspace_mounts = [
            mount
            for mount in mounts
            if isinstance(mount, dict) and mount.get("Destination") == self.mount_point
        ]
        runtime_mounts = [
            mount
            for mount in mounts
            if isinstance(mount, dict)
            and mount.get("Destination") == self.runtime_mount_point
        ]
        docker_socket_mounts = [
            mount
            for mount in mounts
            if isinstance(mount, dict)
            and "docker.sock"
            in (str(mount.get("Source") or "") + str(mount.get("Destination") or ""))
        ]
        checks = {
            "network_matches_requested": host.get("NetworkMode") == self.network,
            "privileged_is_false": host.get("Privileged") is False,
            "pid_namespace_not_host": host.get("PidMode") != "host",
            "ipc_namespace_not_host": host.get("IpcMode") != "host",
            "workspace_mount_exact": len(workspace_mounts) == 1
            and str(workspace_mounts[0].get("Source") or "") == expected_source
            and workspace_mounts[0].get("RW") is expected_rw,
            "runtime_mount_exact": (
                not runtime_mounts
                if runtime_source is None
                else len(runtime_mounts) == 1
                and str(runtime_mounts[0].get("Source") or "") == runtime_source
                and runtime_mounts[0].get("RW") is expected_runtime_rw
            ),
            "docker_socket_absent": not docker_socket_mounts,
        }
        self.container_security_audit = {
            "status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "actual_network_mode": str(host.get("NetworkMode") or ""),
            "actual_user": str(config.get("User") or ""),
            "mounts": [
                {
                    "type": str(mount.get("Type") or ""),
                    "source": str(mount.get("Source") or ""),
                    "destination": str(mount.get("Destination") or ""),
                    "rw": bool(mount.get("RW")),
                }
                for mount in mounts
                if isinstance(mount, dict)
            ],
        }
        if self.container_security_audit["status"] != "passed":
            self.stop()
            raise RuntimeError("solve container security audit failed")

    def stop(self) -> None:
        if self.container_id:
            container_id = self.container_id
            removed = subprocess.run(
                ["docker", "rm", "-f", container_id],
                capture_output=True,
                text=True,
            )
            if removed.returncode != 0:
                raise RuntimeError(
                    "docker container cleanup failed: "
                    + (removed.stderr.strip() or removed.stdout.strip())
                )
            self.container_id = None

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- execution ---------------------------------------------------------- #

    def exec(
        self,
        command: str,
        *,
        timeout_sec: int | float | None = None,
    ) -> tuple[int, str]:
        if not self.container_id:
            raise RuntimeError("sandbox not started")
        timeout = float(self.exec_timeout if timeout_sec is None else timeout_sec)
        timeout = min(900.0, max(1.0, timeout))
        if self.command_deadline_monotonic is not None:
            timeout = min(timeout, self.command_deadline_monotonic - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("repository command deadline exhausted")
        command_timeout = max(1, int(timeout))
        if self.runtime_dir is not None and self.runtime_venv_relative_path is not None:
            relative = Path(self.runtime_venv_relative_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("runtime venv path must be relative")
            venv = (Path(self.runtime_mount_point) / relative).as_posix()
            quoted_venv = shlex.quote(venv)
            command = (
                f"if [ -d {quoted_venv}/bin ]; then "
                f"export VIRTUAL_ENV={quoted_venv}; "
                f"export PATH={quoted_venv}/bin:$PATH; "
                f"fi; {command}"
            )
        return _run_with_bounded_output(
            [
                "docker",
                "exec",
                self.container_id,
                "timeout",
                "--signal=KILL",
                f"{command_timeout}s",
                "bash",
                "-lc",
                command,
            ],
            timeout_sec=timeout + 2,
        )

    def setup(self, bash_commands: list[str]) -> list[dict]:
        """Run source-runtime setup commands and return per-command results."""
        if not self.container_id:
            self.start()
        results = []
        for cmd in bash_commands:
            rc, out = self.exec(cmd)
            results.append({"command": cmd, "rc": rc, "output": out})
        return results

    def run_check(self, command: str, check_id: str = "") -> CheckResult:
        rc, out = self.exec(command)
        return CheckResult(check_id=check_id, passed=(rc == 0), rc=rc, output=out)
