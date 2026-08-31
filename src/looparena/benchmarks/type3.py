"""Type III package constants and small manifest helpers."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

CASE_SCHEMA = "looparena.type3_case.v2"
SEQUENCE_SCHEMA = "looparena.type3_task_sequence.v2"
INDEX_SCHEMA = "looparena.type3_case_index.v3"
BEYONDSWE_EVALUATOR_SCHEMA = "looparena.type3_beyondswe_official_evaluators.v1"
CANARY_HTML_COMMENT = re.compile(r"^<!--[\s\S]*?-->\r?\n?")
ENTRY_FILE = "%%%ENTRYPOINT:entry_file%%%"
ENTRY_COMMAND = "%%%ENTRYPOINT:entry_command%%%"


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


def canonical_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Keep the first case for each official task and record legacy aliases."""

    selected: list[dict[str, Any]] = []
    canonical_by_task: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for record in records:
        case_id = str(record.get("case_id") or "")
        official_task_id = str(record.get("official_task_id") or "")
        if not case_id or not official_task_id:
            raise ValueError("incomplete Type III case index entry")
        canonical = canonical_by_task.get(official_task_id)
        if canonical is None:
            canonical_by_task[official_task_id] = case_id
            selected.append(record)
        else:
            aliases[case_id] = canonical
    return selected, aliases


def render_scbench_spec(raw: str, entry_file: str) -> str:
    """Render the model-visible form used by the official SCBench runner."""

    rendered = CANARY_HTML_COMMENT.sub("", raw)
    rendered = rendered.replace(ENTRY_FILE, f"{entry_file}.py")
    rendered = rendered.replace(ENTRY_COMMAND, f"python {entry_file}.py")
    return "\n".join(line.rstrip() for line in rendered.splitlines()).rstrip() + "\n"


def all_ordered_checkpoints(problem_config: dict[str, Any]) -> list[str]:
    """Return the complete contiguous checkpoint sequence."""

    checkpoints = problem_config.get("checkpoints")
    if not isinstance(checkpoints, dict) or not checkpoints:
        raise ValueError("official SCBench problem has no checkpoints")
    ordered: list[tuple[int, str]] = []
    for name, raw in checkpoints.items():
        order = raw.get("order") if isinstance(raw, dict) else None
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise ValueError("invalid official SCBench checkpoint order")
        ordered.append((order, str(name)))
    ordered.sort()
    selected = [name for _, name in ordered]
    expected = [f"checkpoint_{index}" for index in range(1, len(selected) + 1)]
    if selected != expected:
        raise ValueError(f"noncontiguous official checkpoint sequence: {selected}")
    return selected


def validate_type3_cohort(cohort_dir: Path) -> dict[str, int]:
    """Validate the small public contract of the complete-task cohort."""

    cohort_dir = cohort_dir.resolve()
    index = read_object(cohort_dir / "CASE_INDEX.json")
    if index.get("schema_version") != INDEX_SCHEMA:
        raise ValueError("unsupported Type III case index")
    cases = index.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Type III case index is empty")

    seen_cases: set[str] = set()
    seen_tasks: set[str] = set()
    counts = {"scbench": 0, "beyondswe_harbor": 0}
    native_steps = 0
    for record in cases:
        case_id = str(record.get("case_id") or "")
        official_task_id = str(record.get("official_task_id") or "")
        if not case_id or case_id in seen_cases:
            raise ValueError(f"invalid or duplicate Type III case: {case_id}")
        if not official_task_id or official_task_id in seen_tasks:
            raise ValueError(f"invalid or duplicate official task: {official_task_id}")
        seen_cases.add(case_id)
        seen_tasks.add(official_task_id)

        case_dir = cohort_dir / "cases" / case_id
        if (case_dir / "workspace.tar.gz").exists() or (
            case_dir / "public_messages.json"
        ).exists():
            raise ValueError(f"{case_id}: Type III must start from the official origin")
        manifest = read_object(case_dir / "case.json")
        sequence = read_object(
            safe_relative(
                case_dir, manifest.get("task_sequence_ref"), "task_sequence_ref"
            )
        )
        if manifest.get("schema_version") != CASE_SCHEMA:
            raise ValueError(f"{case_id}: unsupported case schema")
        if sequence.get("schema_version") != SEQUENCE_SCHEMA:
            raise ValueError(f"{case_id}: unsupported task sequence schema")
        if (
            manifest.get("official_task_id") != official_task_id
            or sequence.get("official_task_id") != official_task_id
        ):
            raise ValueError(f"{case_id}: official task identity mismatch")
        adapter = str(manifest.get("adapter_kind") or "")
        if adapter not in counts or sequence.get("adapter_kind") != adapter:
            raise ValueError(f"{case_id}: unsupported adapter")
        steps = sequence.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(f"{case_id}: empty task sequence")
        for step in steps:
            task_path = safe_relative(case_dir, step.get("task_ref"), "task_ref")
            plan_path = safe_relative(
                case_dir, step.get("evaluator_plan_ref"), "evaluator_plan_ref"
            )
            if not task_path.is_file() or not plan_path.is_file():
                raise ValueError(f"{case_id}: missing task or evaluator plan")
        if adapter == "scbench":
            names = [str(step.get("step_id") or "") for step in steps]
            expected = [f"checkpoint_{number}" for number in range(1, len(steps) + 1)]
            if names != expected or sequence.get("target_step") != names[-1]:
                raise ValueError(f"{case_id}: incomplete checkpoint sequence")
            if sequence.get("official_checkpoint_count") != len(steps):
                raise ValueError(f"{case_id}: checkpoint count mismatch")
        elif len(steps) != 1 or sequence.get("target_step") != "task":
            raise ValueError(f"{case_id}: invalid complete BeyondSWE task")
        counts[adapter] += 1
        native_steps += len(steps)

    aliases = index.get("compatibility_aliases") or {}
    observed = {
        "total": len(cases),
        **counts,
        "native_task_steps": native_steps,
        "compatibility_aliases": len(aliases),
    }
    if index.get("counts") != observed:
        raise ValueError("Type III index counts do not match its cases")
    return observed
