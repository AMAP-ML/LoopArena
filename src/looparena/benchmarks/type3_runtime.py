#!/usr/bin/env python3

"""Shared runtime support for complete official benchmark tasks."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from typing import Any

import yaml
from looparena.paths import repository_root

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility.
    import tomli as tomllib

PORTABLE_ASSETS_ROOT = "/opt/looparena/evaluator_assets"

from looparena.evaluators.base import (  # noqa: E402
    beyondswe_evaluator_network,
    beyondswe_solve_network,
)
from looparena.evaluators.loopsbench import (
    task_tree_sha256 as _loopsbench_task_tree_sha256,
)


def localize_evaluator_plan(
    plan: dict[str, Any],
    *,
    assets_root: Path,
) -> dict[str, Any]:
    """Resolve the stored portable evaluator-assets root for one local run."""

    localized = json.loads(json.dumps(plan))
    assets_root = assets_root.resolve()
    config = localized.get("adapter_config") or {}
    if localized.get("adapter_kind") == "scbench":
        fields = ("runner_root", "problems_root", "env_config", "private_bundle_path")
    elif localized.get("adapter_kind") == "loopsbench":
        fields = ("task_dir", "runtime_assets_dir")
    else:
        fields = ("task_dir",)
    for field in fields:
        raw = config.get(field)
        if not raw:
            continue
        value = str(raw)
        if value == PORTABLE_ASSETS_ROOT:
            config[field] = str(assets_root)
        elif value.startswith(PORTABLE_ASSETS_ROOT + "/"):
            relative = PurePosixPath(value.removeprefix(PORTABLE_ASSETS_ROOT + "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe_evaluator_path:{field}:{value}")
            candidate = assets_root / Path(*relative.parts)
            config[field] = str(candidate)
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
        # Start the immutable image selected by the case.  The human-readable
        # tag is still used while loading the archived image, but another
        # process must not be able to retag the solve between that step and
        # ``docker run``.
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
    elif adapter_kind == "beyondswe_harbor":
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
    elif adapter_kind == "loopsbench":
        image = runtime.get("image_id") or runtime.get("image_reference")
        mount_point = str(runtime.get("workdir") or "/workspace")
        # LoopsBench release images run the solve as root, while the sealed
        # workspace snapshot is copied by the invoking host account. Python
        # and build tools otherwise leave root-owned cache/output files whose
        # source-derived 0600/0640 modes are unreadable on the host. Keep the
        # frozen workspace snapshot exact and normalize only read/traverse bits at
        # the end of every model-issued login shell. The per-user run root is
        # private, and no repository path or content is omitted or rewritten.
        setup_commands = []
        network = plan.get("solve_network") or plan.get("network")
        if mount_point != "/workspace":
            raise ValueError("LoopsBench runtime workdir must be /workspace")
        if plan.get("network") != network:
            raise ValueError("LoopsBench solve and evaluator networks differ")
    else:
        raise ValueError(f"unsupported_evaluator_adapter:{adapter_kind}")
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


def _matching_local_image_id(
    *,
    docker_executable: str,
    reference: str,
    platform: str,
    accepted_ids: set[str],
) -> str | None:
    """Resolve one archived image across Docker image-store implementations."""

    proc = subprocess.run(
        [docker_executable, "image", "inspect", reference],
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
    if actual_id not in accepted_ids or actual_platform != platform:
        return None
    return actual_id


def _accepted_image_ids(profile: dict[str, Any], primary_id: object) -> set[str]:
    return {
        value
        for value in (
            primary_id,
            profile.get("oci_manifest_digest"),
            profile.get("oci_config_digest"),
        )
        if isinstance(value, str) and value
    }


def _scbench_runtime_profile(
    plan: dict[str, Any],
    case_dir: Path,
    profile_name: str,
) -> dict[str, Any]:
    del case_dir
    if profile_name != "canonical-amd64":
        raise ValueError("only the official linux/amd64 SCBench runtime is supported")
    runtime = plan.get("runtime_identity") or {}
    upstream = tomllib.loads(
        (repository_root() / "benchmarks/upstreams.toml").read_text()
    )["scbench"]
    return {
        "reference": runtime.get("evaluation_image_reference"),
        "platform": runtime.get("evaluation_image_platform") or "linux/amd64",
        "recipe_hash": upstream["base_image_recipe_hash"],
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
        recipe_hash=profile["recipe_hash"],
    )
    if image_id is None:
        raise FileNotFoundError(
            "missing or mismatched SCBench runtime image (platform or build recipe); "
            "rebuild it with the pinned runner's `slop-code docker build-base "
            "configs/environments/docker-python3.12-uv.yaml --force-build`"
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


def _loopsbench_image_profile(
    plan: dict[str, Any],
    case_dir: Path,
) -> dict[str, Any]:
    manifest = _read_json(case_dir.parent.parent / "LOOPSBENCH_IMAGE_ASSETS.json")
    profile = (manifest.get("cases") or {}).get(case_dir.name)
    if not isinstance(profile, dict):
        raise ValueError(f"missing LoopsBench image profile for {case_dir.name}")
    runtime = plan.get("runtime_identity") or {}
    config = plan.get("adapter_config") or {}
    task_id = str(profile.get("task_id") or "")
    task_tree_sha256 = str(profile.get("task_tree_sha256") or "")
    if (
        not task_id
        or len(task_tree_sha256) != 64
        or not str(profile.get("image_id") or "")
        or not str(profile.get("image_reference") or "")
        or Path(str(config.get("task_dir") or "")).name != task_id
        or profile.get("image_id") != runtime.get("image_id")
        or profile.get("image_reference") != runtime.get("image_reference")
        or profile.get("platform") != runtime.get("platform")
        or profile.get("platform") != "linux/amd64"
        or profile.get("task_tree_sha256") != runtime.get("task_tree_sha256")
    ):
        raise ValueError(f"LoopsBench image profile does not match {case_dir.name}")
    return profile


def _ensure_loopsbench_image(
    plan: dict[str, Any],
    assets_root: Path,
    profile: dict[str, Any],
) -> str:
    """Require the exact frozen LoopsBench image, restoring its archive if present."""

    runtime = plan.get("runtime_identity") or {}
    config = plan.get("adapter_config") or {}
    docker = str(config.get("docker_executable") or "docker")
    reference = str(runtime.get("image_reference") or "")
    expected_id = str(runtime.get("image_id") or "")
    accepted = _accepted_image_ids(profile, expected_id)
    matched = _matching_local_image_id(
        docker_executable=docker,
        reference=reference,
        platform="linux/amd64",
        accepted_ids=accepted,
    )
    if matched is not None:
        return matched
    raw_relative = profile.get("archive_relative_path_under_assets_root")
    if isinstance(raw_relative, str) and raw_relative:
        relative = Path(raw_relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe_loopsbench_image_archive_path")
        archive = (assets_root / relative).resolve()
        archive.relative_to(assets_root.resolve())
        if archive.is_file():
            loaded = subprocess.run(
                [docker, "load", "--input", str(archive)],
                capture_output=True,
                text=True,
                check=False,
            )
            if loaded.returncode == 0:
                matched = _matching_local_image_id(
                    docker_executable=docker,
                    reference=reference,
                    platform="linux/amd64",
                    accepted_ids=accepted,
                )
                if matched is not None:
                    return matched
    from looparena.commands.submission_runtime import prepared_image

    rebuilt = prepared_image(assets_root, profile, docker=docker)
    if rebuilt is not None:
        runtime["reference_image_id"] = expected_id
        runtime["reference_image_reference"] = reference
        runtime["image_reference"] = rebuilt["image_reference"]
        runtime["runtime_origin"] = "upstream-source-build"
        runtime["source_build_receipt"] = rebuilt
        return str(rebuilt["image_id"])
    raise RuntimeError(
        "LoopsBench image is absent; run looparena-prepare --only loopsbench "
        "--loopsbench-source PATH --assets-root PATH --loopsbench-runtime all "
        f"or load the pinned image {expected_id}"
    )


def _ensure_harbor_image(plan: dict[str, Any], assets_root: Path, profile: dict[str, Any]) -> str:
    """Resolve a pinned BeyondSWE image by tag or immutable object ID."""
    del assets_root
    docker = str((plan.get("adapter_config") or {}).get("docker_executable") or "docker")
    runtime = plan.get("runtime_identity") or {}
    accepted = _accepted_image_ids(profile, runtime.get("image_id"))
    platform = str(runtime.get("platform") or profile.get("platform") or "")
    for reference in [str(profile["image_reference"]), *sorted(accepted)]:
        matched = _matching_local_image_id(
            docker_executable=docker, reference=reference,
            platform=platform, accepted_ids=accepted,
        )
        if matched is not None:
            return matched
    raise RuntimeError("Missing pinned BeyondSWE linux/amd64 image; obtain it from the upstream task instructions")


def _require_evaluator_assets(plan: dict[str, Any]) -> None:
    """Fail before model inference when required private assets are absent."""

    config = plan.get("adapter_config") or {}
    runtime = plan.get("runtime_identity") or {}
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
    elif kind == "loopsbench":
        task_dir = Path(str(config["task_dir"])) if config.get("task_dir") else None
        runtime_assets = (
            Path(str(config["runtime_assets_dir"]))
            if config.get("runtime_assets_dir")
            else None
        )
        from looparena.commands.submission_runtime import verify_dependencies

        if runtime_assets is None:
            raise FileNotFoundError("missing LoopsBench runtime assets")
        verify_dependencies(repository_root(), runtime_assets)
        required = [
            ("task_dir", task_dir, "directory"),
            ("task_config", task_dir / "task.yaml" if task_dir else None, "file"),
            ("unit_dag", task_dir / "unit_dag.json" if task_dir else None, "file"),
            (
                "test_entrypoint",
                task_dir / "run-tests.sh" if task_dir else None,
                "file",
            ),
            ("tests", task_dir / "tests" if task_dir else None, "directory"),
            ("runtime_assets", runtime_assets, "directory"),
            (
                "runtime_uv",
                runtime_assets / "bin" / "uv" if runtime_assets else None,
                "file",
            ),
            (
                "runtime_wheelhouse",
                runtime_assets / "wheelhouse" if runtime_assets else None,
                "directory",
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
    if kind == "loopsbench" and _loopsbench_task_tree_sha256(task_dir) != (
        runtime.get("task_tree_sha256")
    ):
        raise ValueError("LoopsBench task tree does not match the frozen digest")
    if kind == "scbench":
        from looparena.commands.submission_assets import _validate_scbench_sources
        _validate_scbench_sources(
            root=repository_root(), runner=Path(str(config["runner_root"])), source_root=problems_root
        )
        uv_executable = str(config.get("uv_executable") or "uv")
        if shutil.which(uv_executable) is None:
            raise FileNotFoundError(
                "missing SCBench host evaluator executable: "
                f"{uv_executable}; install with "
                "`python3 -m pip install -e '.[gateway,scbench]'`"
            )


def _local_image_id(
    *, docker_executable: str, reference: str, platform: str, recipe_hash: str
) -> str | None:
    """Resolve a SCBench image matching the platform and upstream recipe label."""

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
    labels = (image.get("Config") or {}).get("Labels") or {}
    if (
        actual_platform != platform
        or labels.get("io.slop-code.base-image-hash") != recipe_hash
    ):
        return None
    return actual_id
