#!/usr/bin/env python3
"""Coding tools, repository execution, deadlines, and response metadata.

Both main-worker arms receive the same ordinary coding-tool catalog. The
controlled arm hands the current inner loop back by ending naturally; the
reporter receives a separate read-only catalog ending in ``round_report``.
"""

from __future__ import annotations

import copy
import inspect
import json
import re
import shlex
import signal
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from looparena.harness import prompts as harness_prompts

WORKER_SYSTEM = harness_prompts.WORKER_SYSTEM_PROMPT

MAX_CONSECUTIVE_EMPTY_ASSISTANT_RESPONSES = 3
DEFAULT_COMMAND_TIMEOUT_SEC = 120
MAX_COMMAND_TIMEOUT_SEC = 900
TOOL_OUTPUT_HEAD_BYTES = 8 * 1024
TOOL_OUTPUT_TAIL_BYTES = 24 * 1024
TOOL_OUTPUT_INLINE_BYTES = TOOL_OUTPUT_HEAD_BYTES + TOOL_OUTPUT_TAIL_BYTES
DEFAULT_READ_LINES = 2000
MAX_READ_LINES = 2000
MAX_LINE_CHARS = 2000
TEXT_SCAN_CHARS = 64 * 1024
LINE_SCAN_CHARS = 8 * 1024

REPOSITORY_TOOL_CATALOG = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a line-numbered repository file window. With no window arguments, returns the first 2000 lines. Use start_line/max_lines to continue through long files; a truncated window reports the remaining lines and the next page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_file",
            "description": "View a line-numbered repository file window. With no window arguments, returns the first 2000 lines. Use start_line/max_lines to page, or center_line/context_lines around search hits or tracebacks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "center_line": {"type": "integer", "minimum": 1},
                    "context_lines": {"type": "integer", "minimum": 0, "maximum": 500},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories under a repository path. If the listing is too large, the output says so and asks you to list a narrower subdirectory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search repository text and return line-numbered matches for follow-up view_file calls. If matches are omitted, the output says so; narrow the query/path or increase max_results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "Search repository text and return line-numbered matches for follow-up view_file calls. If matches are omitted, the output says so; narrow the query/path or increase max_results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Create or edit one repository file using create, str_replace, or insert.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "command": {
                        "type": "string",
                        "enum": ["create", "str_replace", "insert"],
                    },
                    "file_text": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                    "insert_line": {"type": "integer"},
                },
                "required": ["path", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Compatibility alias for apply_patch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "command": {
                        "type": "string",
                        "enum": ["create", "str_replace", "insert"],
                    },
                    "file_text": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                    "insert_line": {"type": "integer"},
                },
                "required": ["path", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create",
            "description": "Create or replace one repository file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "file_text": {"type": "string"},
                },
                "required": ["path", "file_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "Replace one exact, unique string in an existing repository file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert",
            "description": "Insert text at a zero-based line index in an existing repository file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "insert_line": {"type": "integer"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "insert_line", "new_str"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one foreground bash command, initially from the repository working directory. timeout_sec defaults to 120 and may be raised to 900 for a legitimately long foreground build or test; this still costs one tool turn. Very long output is shown as a bounded head-and-tail view; rerun a narrower command or redirect output to a repository file when you need a specific omitted section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 900},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_service_check",
            "description": "Temporarily run one foreground service while one check command executes, then always stop the service before this tool call returns. Use only when a test genuinely needs a local service.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_command": {"type": "string"},
                    "check_command": {"type": "string"},
                    "ready_delay_sec": {"type": "integer", "minimum": 0, "maximum": 30},
                    "timeout_sec": {"type": "integer", "minimum": 1, "maximum": 900},
                },
                "required": ["service_command", "check_command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_status",
            "description": "Show git status for the repository; returns a clear note when the workspace is not a git repository.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diff",
            "description": "Show git diff for the repository or one path; returns a clear note when the workspace is not a git repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
        },
    },
]
REPO_TOOL_NAMES = {
    "read_file",
    "view_file",
    "list_files",
    "search",
    "search_text",
    "apply_patch",
    "edit_file",
    "create",
    "str_replace",
    "insert",
    "run_command",
    "run_service_check",
    "get_status",
    "get_diff",
}


class RepositoryToolArgumentError(ValueError):
    """A worker-correctable repository tool argument error."""


def _rel_path(path: str, mount_point: str) -> str:
    """Normalize a worker-supplied path to one relative to the host workdir."""
    if mount_point and path.startswith(mount_point):
        path = path[len(mount_point) :]
    return path.lstrip("/")


def _repo_path(path: str, workdir: Path, mount_point: str) -> Path:
    """Resolve a worker path and reject paths outside the restored repository."""
    if not isinstance(path, str):
        raise RepositoryToolArgumentError(
            f"path must be a string, got {type(path).__name__}"
        )
    root = workdir.resolve()
    candidate = (root / _rel_path(path, mount_point)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RepositoryToolArgumentError(f"path outside repo: {path}") from exc
    return candidate


def _apply_patch(args: dict, workdir: Path, mount_point: str) -> str:
    try:
        path = _repo_path(args.get("path", ""), workdir, mount_point)
    except ValueError as exc:
        return f"error: {exc}"
    command = args.get("command")
    if command == "create":
        contents = args.get("file_text") or args.get("new_str") or ""
        if not isinstance(contents, str):
            return f"error: file_text must be a string, got {type(contents).__name__}"
        if path.exists() and not path.is_file():
            return f"error: path is not a regular file: {args.get('path')}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return f"created {path.name}"
    if command == "str_replace":
        if not path.exists():
            return f"error: file not found: {args.get('path')}"
        if path.is_dir():
            return f"error: path is a directory: {args.get('path')}"
        if not path.is_file():
            return f"error: path is not a regular file: {args.get('path')}"
        text = path.read_text(encoding="utf-8")
        old = args.get("old_str", "")
        new = args.get("new_str", "")
        if not isinstance(old, str) or not isinstance(new, str):
            return "error: old_str and new_str must be strings"
        if text.count(old) != 1:
            return f"error: old_str must match exactly once (matched {text.count(old)})"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return "edited"
    if command == "insert":
        if not path.exists():
            return f"error: file not found: {args.get('path')}"
        if path.is_dir():
            return f"error: path is a directory: {args.get('path')}"
        if not path.is_file():
            return f"error: path is not a regular file: {args.get('path')}"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        raw_insert_line = args.get("insert_line")
        if raw_insert_line is None:
            idx = len(lines)
        elif isinstance(raw_insert_line, int) and not isinstance(raw_insert_line, bool):
            idx = raw_insert_line
        else:
            return f"error: insert_line must be an integer, got {type(raw_insert_line).__name__}"
        new = args.get("new_str", "")
        if not isinstance(new, str):
            return f"error: new_str must be a string, got {type(new).__name__}"
        lines.insert(idx, new + "\n")
        path.write_text("".join(lines), encoding="utf-8")
        return "inserted"
    return f"error: unknown apply_patch command {command}"


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _sandbox_exec(
    sandbox: Any,
    command: str,
    *,
    timeout_sec: int | None = None,
) -> tuple[int, str]:
    """Use an explicit timeout when the sandbox supports it."""

    if timeout_sec is None:
        return sandbox.exec(command)
    try:
        parameters = inspect.signature(sandbox.exec).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "timeout_sec" in parameters:
        return sandbox.exec(command, timeout_sec=timeout_sec)
    return sandbox.exec(command)


def _decode_utf8_window(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _utf8_prefix(value: str, max_bytes: int) -> tuple[str, int]:
    """Return a prefix ending on a UTF-8 boundary and its encoded byte count."""

    payload = value.encode("utf-8", errors="replace")
    if len(payload) <= max_bytes:
        return value, len(payload)
    end = max_bytes
    while end > 0:
        try:
            return payload[:end].decode("utf-8"), end
        except UnicodeDecodeError:
            end -= 1
    return "", 0


def _format_bounded_prefix(
    output: str,
    *,
    label: str,
    guidance: str,
) -> str:
    """Bound a listing/search response without silently hiding omitted bytes."""

    payload_bytes = len(output.encode("utf-8", errors="replace"))
    if payload_bytes <= TOOL_OUTPUT_INLINE_BYTES:
        return output
    prefix, shown_bytes = _utf8_prefix(output, TOOL_OUTPUT_INLINE_BYTES)
    # Listing and search output are record-oriented. Avoid presenting the tail
    # of one record as though it were complete when a newline is available.
    if "\n" in prefix:
        prefix = prefix[: prefix.rfind("\n") + 1]
        shown_bytes = len(prefix.encode("utf-8"))
    omitted_bytes = payload_bytes - shown_bytes
    return (
        prefix.rstrip("\n")
        + f"\n... [{label} output truncated: shown_bytes={shown_bytes}; "
        f"omitted_bytes={omitted_bytes}. {guidance}]"
    )


def _format_command_result(
    sandbox: Any,
    rc: int,
    output: str,
) -> str:
    """Return useful bounded head-and-tail context."""

    payload = output.encode("utf-8", errors="replace")
    if len(payload) <= TOOL_OUTPUT_INLINE_BYTES:
        return f"[exit {rc}]\n{output}"
    head = _decode_utf8_window(payload[:TOOL_OUTPUT_HEAD_BYTES])
    tail = _decode_utf8_window(payload[-TOOL_OUTPUT_TAIL_BYTES:])
    return (
        f"[exit {rc}]\n"
        f"[output truncated: utf8_bytes={len(payload)}; "
        f"showing first {TOOL_OUTPUT_HEAD_BYTES} and last "
        f"{TOOL_OUTPUT_TAIL_BYTES} bytes]\n"
        f"{head}\n"
        "... [middle omitted; rerun a narrower command or redirect output to "
        "a repository file and inspect the needed lines] ...\n"
        f"{tail}"
    )


def _managed_service_command(
    service_command: str,
    check_command: str,
    *,
    ready_delay_sec: int,
) -> str:
    """Build one shell scope whose temporary service cannot outlive the call."""

    service = shlex.quote(service_command)
    check = shlex.quote(check_command)
    delay = max(0, min(30, int(ready_delay_sec)))
    return (
        "set -u; "
        "service_log=/tmp/looparena-managed-service.log; "
        ': >"$service_log"; '
        f'bash -lc {service} >"$service_log" 2>&1 & '
        "service_pid=$!; "
        "cleanup() { "
        "trap - EXIT INT TERM; "
        "if command -v pkill >/dev/null 2>&1; then "
        'pkill -TERM -P "$service_pid" 2>/dev/null || true; fi; '
        'kill -TERM "$service_pid" 2>/dev/null || true; '
        "sleep 1; "
        "if command -v pkill >/dev/null 2>&1; then "
        'pkill -KILL -P "$service_pid" 2>/dev/null || true; fi; '
        'kill -KILL "$service_pid" 2>/dev/null || true; '
        'wait "$service_pid" 2>/dev/null || true; '
        "}; "
        "trap cleanup EXIT INT TERM; "
        f"sleep {delay}; "
        "set +e; "
        f"bash -lc {check}; "
        "check_rc=$?; "
        "set -e; "
        "cleanup; "
        "printf '\\n[managed service log]\\n'; "
        'tail -c 32768 "$service_log" 2>/dev/null || true; '
        'exit "$check_rc"'
    )


def _run_command_boundary_error(command: str) -> str:
    """Reject detached work while leaving ordinary shell behavior intact."""

    # Scan the ordinary shell quoting states before looking for a bare ``&``.
    # This keeps a literal or escaped ampersand in an argument (for example
    # ``'A&B'``) from being mistaken for a background job while still catching
    # both ``cmd &`` and ``cmd&``. Malformed quoting is left for bash to report.
    quote = ""
    index = 0
    background_operator = False
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == "'":
                quote = ""
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = ""
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "#" and (
            index == 0 or command[index - 1].isspace() or command[index - 1] in ";&|()"
        ):
            newline = command.find("\n", index + 1)
            if newline < 0:
                break
            index = newline + 1
            continue
        if char == "&":
            previous = command[index - 1] if index else ""
            following = command[index + 1] if index + 1 < len(command) else ""
            if previous not in {"&", "<", ">"} and following not in {"&", ">"}:
                background_operator = True
                break
        index += 1

    if background_operator:
        return "run_command may not start background processes"

    # Tokenization is still useful for recognizing detached command names at a
    # real command boundary without matching the same words inside quoted text.
    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars="&<>|;()\n",
        )
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        shell_tokens = list(lexer)
    except ValueError:
        shell_tokens = []

    detached_commands = {
        "nohup",
        "disown",
        "setsid",
        "daemonize",
        "systemd-run",
        "at",
        "batch",
        "crontab",
    }
    command_start = True
    for token in shell_tokens:
        if token in detached_commands and command_start:
            return "run_command may not detach or schedule background processes"
        if token and all(char in ";&|()\n" for char in token):
            command_start = any(char in ";&|(\n" for char in token)
        elif token not in {"<", ">", "<<", ">>", "<>", ">&", "<&", "&>"}:
            command_start = False
    return ""


def _redact_error_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"sk-[A-Za-z0-9._-]{12,}", "[REDACTED]", text)
    text = re.sub(
        r"(api[_-]?key|authorization|bearer)[\s'\":=]+[^\s,'\"]+",
        r"\1=[REDACTED]",
        text,
        flags=re.I,
    )
    return re.sub(r"/Users/[A-Za-z0-9_.:/\\-]+", "[local-path]", text)


def _count_text_lines(path: Path) -> int:
    """Count logical text lines in bounded memory, including a final partial line."""

    newline_count = 0
    saw_text = False
    final_character = ""
    with path.open("r", encoding="utf-8", errors="replace", newline=None) as handle:
        while chunk := handle.read(TEXT_SCAN_CHARS):
            saw_text = True
            newline_count += chunk.count("\n")
            final_character = chunk[-1]
    if saw_text and final_character != "\n":
        newline_count += 1
    return newline_count


def _read_line_preview(handle: Any) -> tuple[str, int] | None:
    """Read one logical line without retaining more than its displayed prefix."""

    prefix_parts: list[str] = []
    prefix_chars = 0
    total_chars = 0
    saw_text = False
    while True:
        chunk = handle.readline(LINE_SCAN_CHARS)
        if chunk == "":
            if not saw_text:
                return None
            break
        saw_text = True
        ends_line = chunk.endswith("\n")
        content = chunk[:-1] if ends_line else chunk
        total_chars += len(content)
        if prefix_chars < MAX_LINE_CHARS:
            visible = content[: MAX_LINE_CHARS - prefix_chars]
            prefix_parts.append(visible)
            prefix_chars += len(visible)
        if ends_line:
            break
    return "".join(prefix_parts), total_chars


def _read_numbered_text_window(
    path: Path,
    *,
    start_line: int,
    end_line: int,
    total_lines: int,
) -> tuple[str, int]:
    """Read one numbered line range in bounded memory.

    Individual pathological lines use a Claude Code-style cap, but every such
    cap is declared inline with the omitted count.
    """

    rendered: list[str] = []
    clipped_lines = 0
    width = max(1, len(str(total_lines)))
    with path.open("r", encoding="utf-8", errors="replace", newline=None) as handle:
        line_number = 0
        while line_number < end_line:
            preview = _read_line_preview(handle)
            if preview is None:
                break
            line_number += 1
            if line_number < start_line:
                continue
            text, total_chars = preview
            if total_chars > len(text):
                clipped_lines += 1
                text += (
                    f" ... [line clipped: {total_chars - len(text)} characters omitted]"
                )
            rendered.append(f"{line_number:>{width}} | {text}")
    return "\n".join(rendered), clipped_lines


def _read_file_window(
    args: dict,
    workdir: Path,
    mount_point: str,
    *,
    tool_name: str,
) -> str:
    try:
        path = _repo_path(args.get("path", ""), workdir, mount_point)
    except ValueError as exc:
        return f"error: {exc}"
    if not path.exists():
        return f"error: not found: {args.get('path')}"
    if path.is_dir():
        return f"error: path is a directory: {args.get('path')}"
    if not path.is_file():
        return f"error: path is not a regular file: {args.get('path')}"
    try:
        total_lines = _count_text_lines(path)
    except OSError as exc:
        return f"error: cannot read file {args.get('path')}: {exc}"
    if total_lines == 0:
        return "(empty file; total_lines=0)"

    if args.get("center_line") is not None:
        center = _bounded_int(
            args.get("center_line"),
            1,
            minimum=1,
            maximum=total_lines,
        )
        context = _bounded_int(args.get("context_lines"), 40, minimum=0, maximum=500)
        start_line = max(1, center - context)
        end_line = min(total_lines, center + context)
    else:
        requested_start = _bounded_int(
            args.get("start_line"),
            1,
            minimum=1,
            maximum=sys.maxsize,
        )
        if requested_start > total_lines:
            return (
                f"(no lines returned: requested start_line={requested_start} "
                f"is past EOF; total_lines={total_lines})"
            )
        start_line = requested_start
        max_lines = _bounded_int(
            args.get("max_lines"),
            DEFAULT_READ_LINES,
            minimum=1,
            maximum=MAX_READ_LINES,
        )
        end_line = min(total_lines, start_line + max_lines - 1)

    try:
        body, clipped_lines = _read_numbered_text_window(
            path,
            start_line=start_line,
            end_line=end_line,
            total_lines=total_lines,
        )
    except OSError as exc:
        return f"error: cannot read file {args.get('path')}: {exc}"

    if clipped_lines:
        body += (
            f"\n... [{clipped_lines} returned line(s) exceeded "
            f"{MAX_LINE_CHARS} characters and were explicitly clipped]"
        )
    remaining_lines = total_lines - end_line
    if remaining_lines:
        path_arg = json.dumps(str(args.get("path", "")), ensure_ascii=False)
        body += (
            f"\n... [showing lines {start_line}-{end_line} of {total_lines}; "
            f"{remaining_lines} lines remain. Continue with "
            f"{tool_name}(path={path_arg}, start_line={end_line + 1}, "
            f"max_lines={DEFAULT_READ_LINES})]"
        )
    return body


def _exec_tool(
    name: str,
    args: dict,
    sandbox: Any,
    workdir: Path,
    mount_point: str,
    *,
    check_catalog: dict[str, str] | None = None,
) -> str:
    def git_output(rc: int, out: str) -> str:
        if rc != 0 and "not a git repository" in out.lower():
            return "[exit 128]\nworkspace is not a git repository; no git status/diff is available."
        return _format_command_result(sandbox, rc, out)

    if name in {"read_file", "view_file"}:
        return _read_file_window(args, workdir, mount_point, tool_name=name)
    if name == "list_files":
        try:
            p = _repo_path(args.get("path", "."), workdir, mount_point)
        except ValueError as exc:
            return f"error: {exc}"
        rel = p.relative_to(workdir.resolve())
        rc, out = sandbox.exec(f"ls -la {shlex.quote(str(rel) or '.')}")
        return _format_bounded_prefix(
            out,
            label="list_files",
            guidance="List a narrower subdirectory to see the omitted entries.",
        )
    if name in {"search", "search_text"}:
        q = args.get("query", "")
        max_results = _bounded_int(args.get("max_results"), 40, minimum=1, maximum=200)
        try:
            p = _repo_path(args.get("path", "."), workdir, mount_point)
        except ValueError as exc:
            return f"error: {exc}"
        rel = p.relative_to(workdir.resolve())
        rc, out = sandbox.exec(
            f"grep -RIn -- {shlex.quote(str(q))} {shlex.quote(str(rel) or '.')} "
            f"2>/dev/null | head -{max_results + 1}"
        )
        result_lines = out.splitlines()
        more_results = len(result_lines) > max_results
        bounded_results = "\n".join(result_lines[:max_results])
        if more_results:
            bounded_results += (
                f"\n... [search results truncated at {max_results} matches; "
                "at least one additional match exists. Narrow query/path or "
                "increase max_results up to 200.]"
            )
        if not bounded_results:
            return "(no matches)"
        return _format_bounded_prefix(
            bounded_results,
            label="search",
            guidance=(
                "Narrow query/path or lower max_results so complete matching "
                "records fit in the response."
            ),
        )
    if name in {"apply_patch", "edit_file"}:
        return _apply_patch(args, workdir, mount_point)
    if name in {"create", "str_replace", "insert"}:
        return _apply_patch({**args, "command": name}, workdir, mount_point)
    if name == "run_command":
        command = str(args.get("command") or "")
        boundary_error = _run_command_boundary_error(command)
        if boundary_error:
            return f"error: {boundary_error}"
        timeout_sec = _bounded_int(
            args.get("timeout_sec"),
            int(getattr(sandbox, "exec_timeout", DEFAULT_COMMAND_TIMEOUT_SEC)),
            minimum=1,
            maximum=MAX_COMMAND_TIMEOUT_SEC,
        )
        rc, out = _sandbox_exec(
            sandbox,
            command,
            timeout_sec=timeout_sec,
        )
        return _format_command_result(sandbox, rc, out)
    if name == "run_service_check":
        service_command = str(args.get("service_command") or "")
        check_command = str(args.get("check_command") or "")
        if not service_command.strip() or not check_command.strip():
            return "error: service_command and check_command must be non-empty"
        for command in (service_command, check_command):
            boundary_error = _run_command_boundary_error(command)
            if boundary_error:
                return f"error: {boundary_error}"
        timeout_sec = _bounded_int(
            args.get("timeout_sec"),
            int(getattr(sandbox, "exec_timeout", DEFAULT_COMMAND_TIMEOUT_SEC)),
            minimum=1,
            maximum=MAX_COMMAND_TIMEOUT_SEC,
        )
        ready_delay_sec = _bounded_int(
            args.get("ready_delay_sec"),
            1,
            minimum=0,
            maximum=30,
        )
        rc, out = _sandbox_exec(
            sandbox,
            _managed_service_command(
                service_command,
                check_command,
                ready_delay_sec=ready_delay_sec,
            ),
            timeout_sec=timeout_sec,
        )
        return _format_command_result(sandbox, rc, out)
    if name == "get_status":
        rc, out = sandbox.exec("git status --short")
        return git_output(rc, out)
    if name == "get_diff":
        raw_path = str(args.get("path") or "").strip()
        if raw_path:
            try:
                p = _repo_path(raw_path, workdir, mount_point)
            except ValueError as exc:
                return f"error: {exc}"
            rel = p.relative_to(workdir.resolve())
            command = f"git diff -- {shlex.quote(str(rel))}"
        else:
            command = "git diff -- ."
        rc, out = sandbox.exec(command)
        return git_output(rc, out)
    return f"error: unknown tool {name}"


def _exec_tool_event(
    name: str,
    args: dict,
    sandbox: Any,
    workdir: Path,
    mount_point: str,
    *,
    check_catalog: dict[str, str] | None = None,
) -> tuple[str, int | None, bool]:
    """Execute a tool and return result, rc, and whether the result is a tool error."""
    try:
        if name == "run_command":
            command = str(args.get("command") or "")
            boundary_error = _run_command_boundary_error(command)
            if boundary_error:
                return f"error: {boundary_error}", None, False
            timeout_sec = _bounded_int(
                args.get("timeout_sec"),
                int(getattr(sandbox, "exec_timeout", DEFAULT_COMMAND_TIMEOUT_SEC)),
                minimum=1,
                maximum=MAX_COMMAND_TIMEOUT_SEC,
            )
            rc, out = _sandbox_exec(
                sandbox,
                command,
                timeout_sec=timeout_sec,
            )
            return _format_command_result(sandbox, rc, out), rc, False
        if name == "run_service_check":
            service_command = str(args.get("service_command") or "")
            check_command = str(args.get("check_command") or "")
            if not service_command.strip() or not check_command.strip():
                return (
                    "error: service_command and check_command must be non-empty",
                    None,
                    False,
                )
            for command in (service_command, check_command):
                boundary_error = _run_command_boundary_error(command)
                if boundary_error:
                    return f"error: {boundary_error}", None, False
            timeout_sec = _bounded_int(
                args.get("timeout_sec"),
                int(getattr(sandbox, "exec_timeout", DEFAULT_COMMAND_TIMEOUT_SEC)),
                minimum=1,
                maximum=MAX_COMMAND_TIMEOUT_SEC,
            )
            rc, out = _sandbox_exec(
                sandbox,
                _managed_service_command(
                    service_command,
                    check_command,
                    ready_delay_sec=_bounded_int(
                        args.get("ready_delay_sec"),
                        1,
                        minimum=0,
                        maximum=30,
                    ),
                ),
                timeout_sec=timeout_sec,
            )
            return _format_command_result(sandbox, rc, out), rc, False
        result = _exec_tool(
            name, args, sandbox, workdir, mount_point, check_catalog=check_catalog
        )
        # A safely rejected or unsuccessful ordinary repository operation is
        # observable worker feedback, not a harness protocol violation.
        return result, None, False
    except Exception as exc:
        detail = _redact_error_text(f"{type(exc).__name__}: {exc}")
        return f"error: tool execution failed for {name}: {detail}", None, True


def _nonempty_tool_result(name: str, result: str) -> str:
    """Keep valid zero-byte results representable in chat message history."""

    if result != "":
        return result
    if name in {"read_file", "view_file"}:
        return "[empty file]"
    return "[tool completed successfully with no output]"


def _decode_tool_arguments(
    tool_call: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    raw = (tool_call.get("function") or {}).get("arguments") or "{}"
    if isinstance(raw, dict):
        return dict(raw), ""
    try:
        value = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, f"{type(exc).__name__}:{exc}"
    if not isinstance(value, dict):
        return None, "tool arguments must decode to an object"
    return value, ""


def _deadline_exceeded(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _remaining_deadline_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


@contextmanager
def _hard_timeout(seconds: float | None):
    if (
        seconds is None
        or seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def _raise_timeout(signum, frame):
        raise TimeoutError("worker model call exceeded wall-time deadline")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _chat_with_deadline(
    worker: Any,
    messages: list[dict],
    tools: list[dict] | None,
    *,
    seed: int,
    deadline: float | None,
    max_tokens: int,
) -> dict:
    remaining = _remaining_deadline_seconds(deadline)
    if remaining is not None and remaining <= 0:
        raise TimeoutError("worker round wall-time limit reached before model call")
    old_timeout = getattr(worker, "timeout_sec", None)
    changed_timeout = False
    if remaining is not None and old_timeout is not None:
        bounded_timeout = max(0.001, min(float(old_timeout), remaining))
        if bounded_timeout != old_timeout:
            try:
                setattr(worker, "timeout_sec", bounded_timeout)
                changed_timeout = True
            except Exception:
                changed_timeout = False
    call_timeout = remaining
    if old_timeout is not None:
        call_timeout = (
            float(old_timeout)
            if call_timeout is None
            else min(float(old_timeout), call_timeout)
        )
    try:
        with _hard_timeout(call_timeout):
            try:
                parameters = list(inspect.signature(worker.chat).parameters.values())
                supports_kwargs = any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
                supports_deadline = supports_kwargs or any(
                    parameter.name == "deadline_monotonic" for parameter in parameters
                )
                supports_max_tokens = supports_kwargs or any(
                    parameter.name == "max_tokens" for parameter in parameters
                )
            except (TypeError, ValueError):
                supports_deadline = False
                supports_max_tokens = False
            call_kwargs: dict[str, Any] = {"seed": seed}
            if supports_deadline:
                call_kwargs["deadline_monotonic"] = deadline
            if supports_max_tokens:
                call_kwargs["max_tokens"] = max_tokens
            return worker.chat(messages, tools, **call_kwargs)
    finally:
        if changed_timeout:
            setattr(worker, "timeout_sec", old_timeout)


def _assistant_history_message(message: dict[str, Any]) -> dict[str, Any]:
    """Keep private transport metadata out of the next gateway request."""

    history = {
        "role": "assistant",
        "content": str(message.get("content") or ""),
    }
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        normalized_calls = copy.deepcopy(tool_calls)
        for tool_call in normalized_calls:
            function = (
                tool_call.get("function") if isinstance(tool_call, dict) else None
            )
            if not isinstance(function, dict):
                continue
            raw = function.get("arguments") or "{}"
            if isinstance(raw, dict):
                function["arguments"] = json.dumps(
                    raw, ensure_ascii=False, sort_keys=True
                )
                continue
            try:
                decoded = json.loads(str(raw))
                if not isinstance(decoded, dict):
                    raise ValueError("tool arguments must be an object")
            except (json.JSONDecodeError, TypeError, ValueError):
                raw_text = str(raw)
                function["arguments"] = json.dumps(
                    {
                        "looparena_rejected_malformed_arguments": True,
                        "original_arguments_utf8_bytes": len(
                            raw_text.encode("utf-8", errors="replace")
                        ),
                    },
                    sort_keys=True,
                )
        history["tool_calls"] = normalized_calls
    response_items = message.get("_looparena_openai_response_items")
    if response_items:
        # Reasoning models require their opaque Responses reasoning items to be
        # replayed with function outputs. They are transport state, not exposed
        # as assistant text or executable tool calls.
        history["_looparena_openai_response_items"] = copy.deepcopy(response_items)
    return history


def _response_audit(message: dict[str, Any]) -> dict[str, Any]:
    audit = message.get("_looparena_response_audit")
    if isinstance(audit, dict):
        out = dict(audit)
        context_audit = message.get("_looparena_context_audit")
        if isinstance(context_audit, dict):
            out["context_audit"] = dict(context_audit)
        attempts = message.get("_looparena_gateway_attempts")
        if isinstance(attempts, int) and not isinstance(attempts, bool):
            out["gateway_attempts"] = attempts
        credential_attempts = message.get("_looparena_gateway_credential_attempts")
        if isinstance(credential_attempts, list):
            out["gateway_credential_attempts"] = copy.deepcopy(credential_attempts)
        return out
    tool_calls = message.get("tool_calls") or []
    return {
        "transport": "unannotated_client",
        "finish_reason": "",
        "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
    }


REPORTER_ROUND_REPORT_TOOL = {
    "type": "function",
    "function": {
        "name": "round_report",
        "description": "Submit the factual handoff for the supervising agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_context_and_constraints": {
                    "type": "string",
                    "description": (
                        "Markdown with clearly labeled `Overall repository task` and "
                        "`Current assignment` subsections. Keep exact end-to-end task "
                        "requirements separate from constraints that apply only to the "
                        "latest bounded work slice; never promote an assignment-only "
                        "instruction, hypothesis, or prohibition into the overall "
                        "repository task."
                    ),
                },
                "work_history_and_current_state": {
                    "type": "string",
                    "description": (
                        "Markdown beginning with the latest assignment, what the coding "
                        "agent actually did after it, and the resulting current state; "
                        "include and label earlier history only when it remains relevant."
                    ),
                },
                "verification_and_evidence": {
                    "type": "string",
                    "description": (
                        "Markdown stating for each consequential check what ran or was "
                        "inspected, the observed result, what it covers, and what it does "
                        "not establish; never generalize a focused or public check into "
                        "broader test or private-evaluation success. Cite the shown "
                        "coding-agent turns needed to support, qualify, or contradict "
                        "consequential claims. Use [E12] for one turn, [E12, E13] "
                        "for separate turns, or [E12-E15] for every turn in one "
                        "continuous range; select them by relevance and evidentiary "
                        "value, not to meet a target count. At least one valid citation "
                        "is required when E labels are shown. Only complete square-"
                        "bracket forms select evidence: [E23], [E23, E25], or "
                        "[E23-E25]. A bare or parenthesized label such as E23 or "
                        "(E23) is ordinary prose and does not select evidence."
                    ),
                },
                "open_issues_and_uncertainty": {
                    "type": "string",
                    "description": (
                        "Markdown describing only unresolved requirements, failures, "
                        "blockers, risks, contradictions, and missing evidence; do not "
                        "include remedies, recommendations, possible paths, next steps, "
                        "proposed implementations, or instructions."
                    ),
                },
            },
            "required": [
                "task_context_and_constraints",
                "work_history_and_current_state",
                "verification_and_evidence",
                "open_issues_and_uncertainty",
            ],
            "additionalProperties": False,
        },
    },
}

MAIN_REPOSITORY_TOOLS = [
    tool
    for tool in REPOSITORY_TOOL_CATALOG
    if tool["function"]["name"] in REPO_TOOL_NAMES
]
REPORTER_TOOLS = [
    tool
    for tool in REPOSITORY_TOOL_CATALOG
    if tool["function"]["name"]
    in {
        "read_file",
        "view_file",
        "list_files",
        "search",
        "search_text",
        "get_status",
        "get_diff",
    }
] + [
    REPORTER_ROUND_REPORT_TOOL,
]


def _main_event_id(turn_index: int, tool_index: int, name: str) -> str:
    safe_name = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "tool"
    return f"main_event:{turn_index}:{tool_index}:{safe_name}"
