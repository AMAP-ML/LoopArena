#!/usr/bin/env python3

"""Shared runtime support for complete official benchmark tasks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

PORTABLE_ASSETS_ROOT = "/opt/looparena/evaluator_assets"

from looparena.evaluators.base import (
    beyondswe_evaluator_network,
    beyondswe_solve_network,
)


def localize_evaluator_plan(
    plan: dict[str, Any],
    *,
    assets_root: Path,
) -> dict[str, Any]:
    """Resolve the stored portable evaluator-assets root for one local run."""

    localized = json.loads(json.dumps(plan))
    config = localized.get("adapter_config") or {}
    fields = (
        ("runner_root", "problems_root", "env_config", "private_bundle_path")
        if localized.get("adapter_kind") == "scbench"
        else ("task_dir",)
    )
    for field in fields:
        raw = config.get(field)
        if not raw:
            continue
        value = str(raw)
        if value == PORTABLE_ASSETS_ROOT:
            config[field] = str(assets_root)
        elif value.startswith(PORTABLE_ASSETS_ROOT + "/"):
            config[field] = str(
                assets_root / value.removeprefix(PORTABLE_ASSETS_ROOT + "/")
            )
        else:
            raise ValueError(f"nonportable_evaluator_path:{field}:{value}")
    return localized


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _solve_runtime(
    plan: dict[str, Any],
) -> tuple[str, str, str, list[str]]:
    runtime = plan.get("runtime_identity") or {}
    adapter_kind = plan.get("adapter_kind")
    if adapter_kind == "scbench":
        # SCBench resumes checkpoints in the public, runner-built base image.
        # ``image_*`` identifies only the raw Dockerfile base; using it drops
        # Git, Node, compilers, and the rest of the official agent environment.
        # Start the immutable local image object resolved during preflight.
        image = runtime.get("evaluation_image_id") or runtime.get(
            "evaluation_image_reference"
        )
        mount_point = "/workspace"
        # These are the pinned SCBench environment's resume_commands.  They
        # deliberately restore .venv inside the resumed workspace, tolerate
        # either command failing or reaching the source runner's 300-second
        # per-command limit, and do not activate .venv for the agent.  The
        # surrounding harness timeout is slightly larger so the inner timeout
        # can return through ``|| true`` just as the source runner logs a
        # warning and continues.
        setup_commands = [
            "timeout --signal=KILL 300s python -m venv .venv || true",
            (
                "timeout --signal=KILL 300s "
                ".venv/bin/pip install -r requirements.txt 2>/dev/null || true"
            ),
        ]
        network = plan.get("network")
    else:
        image = runtime.get("image_id") or runtime.get("image_reference")
        mount_point = str(runtime.get("workdir") or "/work")
        setup_commands = []
        raw_task_dir = str(
            (plan.get("adapter_config") or {}).get("task_dir") or ""
        ).strip()
        if not raw_task_dir:
            raise ValueError("evaluator_plan_has_no_beyondswe_task_dir")
        task_dir = Path(raw_task_dir)
        task_settings = tomllib.loads(
            (task_dir / "task.toml").read_text(encoding="utf-8")
        )
        _, solve_network = beyondswe_solve_network(
            task_settings,
            task_dir=task_dir,
        )
        _, evaluator_network = beyondswe_evaluator_network(
            task_settings,
            task_dir=task_dir,
        )
        if solve_network != evaluator_network:
            raise ValueError(
                "BeyondSWE shared-container solve and evaluator networks differ"
            )
        # The localized official task is the runtime authority. Updating the
        # in-memory plan keeps the solve command and terminal evaluator on the
        # same source-declared network even if a stored plan field is stale.
        plan["solve_network"] = solve_network
        plan["network"] = evaluator_network
        network = solve_network
    if not isinstance(image, str) or not image:
        raise ValueError("evaluator_plan_has_no_solve_image")
    if network not in {"none", "bridge", "host"}:
        raise ValueError("evaluator_plan_has_no_source_network_policy")
    return image, mount_point, str(network), setup_commands


def _materialize_scbench_public_static_assets(
    plan: dict[str, Any],
    workspace: Path,
) -> None:
    """Restore only the public assets declared by the SCBench problem.

    SCBench omits these assets from checkpoint snapshots and writes them back
    whenever a checkpoint is resumed.  The problem directory also contains
    hidden evaluator material, so only paths explicitly listed under
    ``static_assets`` in ``config.yaml`` may cross into the solve workspace.
    """

    if plan.get("adapter_kind") != "scbench":
        return
    config = plan.get("adapter_config") or {}
    problem_dir = Path(str(config["private_bundle_path"])).resolve()
    problem_config = yaml.safe_load(
        (problem_dir / "config.yaml").read_text(encoding="utf-8")
    )
    static_assets = (problem_config or {}).get("static_assets") or {}
    if not isinstance(static_assets, dict):
        raise ValueError("invalid_scbench_static_assets")

    workspace = workspace.resolve()
    for raw_asset in static_assets.values():
        if not isinstance(raw_asset, dict):
            raise ValueError("invalid_scbench_static_asset")
        source_relative = Path(str(raw_asset["path"]))
        target_relative = Path(str(raw_asset.get("save_path") or raw_asset["path"]))
        if (
            source_relative.is_absolute()
            or ".." in source_relative.parts
            or target_relative.is_absolute()
            or ".." in target_relative.parts
        ):
            raise ValueError("unsafe_scbench_static_asset_path")
        source = (problem_dir / source_relative).resolve()
        target = (workspace / target_relative).resolve()
        try:
            source.relative_to(problem_dir)
            target.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("unsafe_scbench_static_asset_path") from exc
        if not source.exists():
            raise FileNotFoundError(f"missing SCBench public static asset: {source}")
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy(source, target)


def _source_solve_limits(
    plan: dict[str, Any],
) -> tuple[float | None, int | None]:
    """Return source-declared Docker CPU and memory limits."""

    if plan.get("adapter_kind") != "beyondswe_harbor":
        # The official SCBench Docker runtime does not impose CPU or memory
        # limits. LoopArena's registered worker budget remains independent.
        return None, None
    config = plan.get("adapter_config") or {}
    task_dir = Path(str(config["task_dir"])).resolve()
    settings = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    environment = settings.get("environment") or {}
    if not isinstance(environment, dict):
        raise ValueError("invalid_beyondswe_task_limits")
    raw_cpus = environment.get("cpus")
    raw_memory = environment.get("memory_mb")
    cpus = (
        float(raw_cpus)
        if isinstance(raw_cpus, (int, float))
        and not isinstance(raw_cpus, bool)
        and raw_cpus > 0
        else None
    )
    memory_mb = (
        int(raw_memory)
        if isinstance(raw_memory, int)
        and not isinstance(raw_memory, bool)
        and raw_memory > 0
        else None
    )
    return cpus, memory_mb


def _local_image_id(
    *, docker_executable: str, reference: str, platform: str
) -> str | None:
    """Return the local image id when its platform matches the benchmark."""

    proc = subprocess.run(
        [docker_executable, "image", "inspect", "--platform", platform, reference],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        image = json.loads(proc.stdout)[0]
        actual_id = str(image["Id"])
        actual_platform = f"{image['Os']}/{image['Architecture']}"
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None
    if actual_platform != platform:
        return None
    return actual_id


def _scbench_runtime_profile(
    plan: dict[str, Any],
    case_dir: Path,
    profile_name: str,
) -> dict[str, Any]:
    del case_dir
    if profile_name != "canonical-amd64":
        raise ValueError("only the official linux/amd64 SCBench runtime is supported")
    runtime = plan.get("runtime_identity") or {}
    return {
        "reference": runtime.get("evaluation_image_reference"),
        "platform": runtime.get("evaluation_image_platform") or "linux/amd64",
    }


def _ensure_scbench_image(
    plan: dict[str, Any],
    assets_root: Path,
    profile: dict[str, Any],
) -> str:
    """Resolve the image built from the pinned official SCBench runner."""

    del assets_root
    docker_executable = str(
        (plan.get("adapter_config") or {}).get("docker_executable") or "docker"
    )
    reference = str(profile.get("reference") or "")
    platform = str(profile.get("platform") or "")
    image_id = _local_image_id(
        docker_executable=docker_executable,
        reference=reference,
        platform=platform,
    )
    if image_id is None:
        raise FileNotFoundError(
            "missing official SCBench linux/amd64 runtime image; build it from "
            "the pinned SCBench runner following its upstream instructions"
        )
    return image_id


def _harbor_image_profile(
    plan: dict[str, Any],
    case_dir: Path,
) -> dict[str, Any]:
    del case_dir
    runtime = plan.get("runtime_identity") or {}
    reference = runtime.get("image_reference")
    if not isinstance(reference, str) or not reference:
        raise ValueError("BeyondSWE plan has no image reference")
    return {
        "image_reference": reference,
        "platform": runtime.get("platform") or "linux/amd64",
    }


def _ensure_harbor_image(
    plan: dict[str, Any],
    assets_root: Path,
    profile: dict[str, Any],
) -> str:
    """Resolve and verify one official public BeyondSWE source image."""

    del assets_root
    docker_executable = str(
        (plan.get("adapter_config") or {}).get("docker_executable") or "docker"
    )
    image_id = _local_image_id(
        docker_executable=docker_executable,
        reference=str(profile["image_reference"]),
        platform=str(profile["platform"]),
    )
    if image_id is None:
        raise RuntimeError(
            "missing official BeyondSWE linux/amd64 source image; pull it "
            "following the pinned upstream task instructions"
        )
    expected_image_id = str((plan.get("runtime_identity") or {}).get("image_id") or "")
    if not expected_image_id:
        raise ValueError("BeyondSWE plan has no pinned image id")
    if image_id != expected_image_id:
        raise RuntimeError(
            "BeyondSWE image identity mismatch for "
            f"{profile['image_reference']}: expected {expected_image_id}, "
            f"found {image_id}; pull or load the pinned linux/amd64 image"
        )
    return image_id


def _require_evaluator_assets(plan: dict[str, Any]) -> None:
    """Fail before model inference when required private assets are absent."""

    config = plan.get("adapter_config") or {}
    kind = plan.get("adapter_kind")
    required: list[tuple[str, Path | None, str]]
    if kind == "scbench":
        private_bundle = (
            Path(str(config["private_bundle_path"]))
            if config.get("private_bundle_path")
            else None
        )
        problems_root = (
            Path(str(config["problems_root"]))
            if config.get("problems_root")
            else private_bundle.parent
            if private_bundle is not None
            else None
        )
        required = [
            (
                "runner_root",
                Path(str(config["runner_root"])) if config.get("runner_root") else None,
                "directory",
            ),
            ("problems_root", problems_root, "directory"),
            ("private_bundle_path", private_bundle, "directory"),
            (
                "env_config",
                Path(str(config["env_config"])) if config.get("env_config") else None,
                "file",
            ),
        ]
    elif kind == "beyondswe_harbor":
        task_dir = Path(str(config["task_dir"])) if config.get("task_dir") else None
        required = [
            ("task_dir", task_dir, "directory"),
            (
                "task_config",
                task_dir / "task.toml" if task_dir is not None else None,
                "file",
            ),
            (
                "test_entrypoint",
                task_dir / "tests" / "test.sh" if task_dir is not None else None,
                "file",
            ),
        ]
    else:
        raise ValueError(f"unsupported_evaluator_adapter:{kind}")

    missing = [
        f"{field}={path if path is not None else '<unset>'} ({expected})"
        for field, path, expected in required
        if not (
            path is not None
            and (path.is_dir() if expected == "directory" else path.is_file())
        )
    ]
    if missing:
        raise FileNotFoundError("missing evaluator assets: " + ", ".join(missing))
    if kind == "scbench":
        uv_executable = str(config.get("uv_executable") or "uv")
        if shutil.which(uv_executable) is None:
            raise FileNotFoundError(
                "missing SCBench host evaluator executable: "
                f"{uv_executable}; install with "
                "`python3 -m pip install -e '.[gateway,scbench]'`"
            )
