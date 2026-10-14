#!/usr/bin/env python3
"""Run one portable Type II case through the request-driven harness."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from looparena.runtime.llm import default_worker_base_url


from looparena.paths import repository_root

ROOT = repository_root()
from looparena.harness.validation import validate_public_conversation  # noqa: E402
from looparena.harness.recovery import (  # noqa: E402
    atomic_write_json,
)
from looparena.benchmarks.type3_runtime import (  # noqa: E402
    _ensure_harbor_image,
    _ensure_scbench_image,
    _harbor_image_profile,
    _materialize_scbench_public_static_assets,
    _require_evaluator_assets,
    _scbench_runtime_profile,
    _solve_runtime,
    _source_solve_limits,
    _ensure_loopsbench_image,
    _loopsbench_image_profile,
    localize_evaluator_plan,
)
from looparena.runtime.sandbox import (  # noqa: E402
    normalize_workspace_archive_root,
    safe_extract_workspace_archive,
    sanitize_workspace_git_history,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_manifest(case_dir: Path) -> dict[str, Any]:
    manifest = _read_json(case_dir / "case.json")
    if manifest.get("case_id") != case_dir.name:
        raise ValueError("type2_case_id_directory_mismatch")
    if not str(manifest.get("sample_id") or "").strip():
        raise ValueError("type2_case_sample_id_missing")
    if manifest.get("start_mode") != "bootstrap_contract_start":
        raise ValueError("unsupported_type2_case_start_mode")
    return manifest


def _workspace_archive(case_dir: Path, assets_root: Path) -> Path:
    local = case_dir / "workspace.tar.gz"
    if local.is_file():
        if (case_dir / "workspace_recipe.json").is_file():
            from looparena.commands.workspaces import verify_archive

            verify_archive(case_dir, local)
        return local
    asset = _read_json(case_dir / "workspace_asset.json")
    relative = Path(str(asset["relative_path_under_assets_root"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe_external_workspace_path")
    archive = (assets_root / relative).resolve()
    try:
        archive.relative_to(assets_root)
    except ValueError as exc:
        raise ValueError("external_workspace_outside_assets_root") from exc
    if not archive.is_file():
        raise FileNotFoundError(
            f"missing external workspace for {case_dir.name}: {archive}"
        )
    from looparena.commands.workspaces import verify_archive

    verify_archive(case_dir, archive)
    return archive


def _restore_beyondswe_git_metadata(
    plan: dict[str, Any],
    workspace: Path,
) -> None:
    """Rebuild a source-native Git view without changing cutpoint files.

    A workspace archive may omit Git metadata or contain a cutpoint commit.
    The source image contains the official repository object database. Replace
    only ``.git`` and point its HEAD/index at the task's public parent commit
    without touching the cutpoint file tree. The history sanitizer removes
    remotes, later refs, reflogs, and unreachable objects.
    """

    if plan.get("adapter_kind") != "beyondswe_harbor":
        return
    git_dir = workspace / ".git"

    config = plan.get("adapter_config") or {}
    task_dir = Path(str(config["task_dir"])).resolve()
    instance = _read_json(task_dir / "tests" / "instance.json")
    parent_commit = str(instance.get("parent_commit") or "").strip()
    source_workdir = str(instance.get("workdir") or "").strip()
    runtime = plan.get("runtime_identity") or {}
    image = str(runtime.get("image_id") or runtime.get("image_reference") or "")
    docker = str(config.get("docker_executable") or "docker")
    if not parent_commit or not source_workdir.startswith("/") or not image:
        raise ValueError("incomplete_beyondswe_git_restore_source")

    created = subprocess.run(
        [
            docker,
            "create",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "sleep",
            image,
            "infinity",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0 or not created.stdout.strip():
        raise RuntimeError(
            "failed to open the BeyondSWE source image for Git restore: "
            + (created.stderr.strip() or created.stdout.strip())
        )
    container_id = created.stdout.strip().splitlines()[-1]
    if git_dir.is_dir() and not git_dir.is_symlink():
        shutil.rmtree(git_dir)
    elif git_dir.exists() or git_dir.is_symlink():
        git_dir.unlink()
    git_dir.mkdir()
    try:
        copied = subprocess.run(
            [
                docker,
                "cp",
                f"{container_id}:{source_workdir}/.git/.",
                str(git_dir),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        subprocess.run(
            [docker, "rm", "-f", container_id],
            capture_output=True,
            text=True,
            check=False,
        )
    if copied.returncode != 0:
        shutil.rmtree(git_dir)
        raise RuntimeError(
            "the BeyondSWE source image has no usable Git metadata: "
            + (copied.stderr.strip() or copied.stdout.strip())
        )
    reset = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "reset",
            "--mixed",
            "--no-refresh",
            parent_commit,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if reset.returncode != 0:
        raise RuntimeError(
            "failed to bind restored Git metadata to the task parent commit: "
            + (reset.stderr.strip() or reset.stdout.strip())
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--arm", required=True, choices=("controlled", "no-control"))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--workspace-dir", type=Path)
    parser.add_argument(
        "--resume-from-attempt",
        type=Path,
        help=(
            "Continue a provider-interrupted attempt from its last durable "
            "worker boundary. The previous workspace and transcript are reused."
        ),
    )
    parser.add_argument(
        "--assets-root",
        type=Path,
        default=os.environ.get("LOOPARENA_ASSETS_ROOT"),
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--worker-model", help="Model ID accepted by the configured endpoint.")
    parser.add_argument("--controller-model", help="Model ID accepted by the configured endpoint.")
    parser.add_argument(
        "--controller-provider",
        choices=("model", "non-adaptive-fixed"),
        default="model",
    )
    parser.add_argument(
        "--scbench-runtime-profile",
        choices=("canonical-amd64",),
        default="canonical-amd64",
        help=("SCBench source runtime; this submission supports linux/amd64."),
    )
    parser.add_argument(
        "--base-url",
        default=default_worker_base_url(),
    )
    parser.add_argument(
        "--controller-base-url",
        default=os.environ.get("LOOPARENA_CONTROLLER_BASE_URL", ""),
    )
    parser.add_argument("--credential-profile-id", default="default-gateway")
    parser.add_argument("--controller-credential-profile-id")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--controller-api-key-env", default="OPENAI_API_KEY")
    parser.add_argument(
        "--worker-wall-time-sec",
        type=int,
        default=7200,
        help=("LoopArena worker wall-time budget (default: 7200 seconds)."),
    )
    parser.add_argument("--reporter-wall-time-sec", type=int, default=900)
    parser.add_argument(
        "--gateway-timeout-sec",
        type=int,
        default=900,
        help=(
            "Per model-call timeout. The 900-second release default avoids "
            "misclassifying slow long-context calls as model failures."
        ),
    )
    parser.add_argument("--tool-timeout-sec", type=int, default=120)
    parser.add_argument("--preflight-only", action="store_true")
    return parser


def preflight_type2_case(
    case_dir: Path,
    *,
    assets_root: Path,
    scbench_runtime_profile: str,
) -> dict[str, str | None]:
    """Resolve one case's declared assets and exact image without solving it."""

    case_dir = case_dir.expanduser().resolve()
    assets_root = assets_root.expanduser().resolve()
    _case_manifest(case_dir)
    prefix_errors = validate_public_conversation(
        _read_json(case_dir / "public_messages.json")
    )
    if prefix_errors:
        raise ValueError(
            "public conversation cannot be replayed: " + "; ".join(prefix_errors)
        )
    stored_plan = _read_json(case_dir / "evaluator_plan.json")
    evaluator_plan = localize_evaluator_plan(
        stored_plan,
        assets_root=assets_root,
    )
    _require_evaluator_assets(evaluator_plan)
    _workspace_archive(case_dir, assets_root)
    _solve_runtime(evaluator_plan)
    resolved_image_id: str | None
    if stored_plan.get("adapter_kind") == "scbench":
        profile = _scbench_runtime_profile(
            stored_plan,
            case_dir,
            scbench_runtime_profile,
        )
        resolved_image_id = _ensure_scbench_image(
            stored_plan,
            assets_root,
            profile,
        )
    elif stored_plan.get("adapter_kind") == "beyondswe_harbor":
        if scbench_runtime_profile != "canonical-amd64":
            raise ValueError("--scbench-runtime-profile applies only to SCBench cases")
        profile = _harbor_image_profile(stored_plan, case_dir)
        resolved_image_id = _ensure_harbor_image(
            stored_plan,
            assets_root,
            profile,
        )
    elif stored_plan.get("adapter_kind") == "loopsbench":
        if scbench_runtime_profile != "canonical-amd64":
            raise ValueError("--scbench-runtime-profile applies only to SCBench cases")
        profile = _loopsbench_image_profile(stored_plan, case_dir)
        resolved_image_id = _ensure_loopsbench_image(
            stored_plan,
            assets_root,
            profile,
        )
    else:
        raise ValueError("unsupported evaluator adapter")
    return {
        "adapter_kind": str(stored_plan.get("adapter_kind") or ""),
        "resolved_image_id": resolved_image_id,
    }


def _prepare_provider_resume(
    *,
    source_attempt: Path,
    out_dir: Path,
    arm: str,
    sample_id: str,
    seed: int,
) -> Path:
    """Continue from the source attempt's last atomic runtime checkpoint."""

    source_attempt = source_attempt.expanduser().resolve()
    checkpoint_path = source_attempt / "recovery_checkpoint.json"
    source_checkpoint = _read_json(checkpoint_path)
    if source_checkpoint.get("safe_to_resume") is not True:
        raise ValueError("resume source has no safe runtime checkpoint")
    if source_checkpoint.get("arm") != arm:
        raise ValueError("resume source arm does not match")
    if source_checkpoint.get("sample_id") != sample_id:
        raise ValueError("resume source sample does not match")
    if int(source_checkpoint.get("seed") or 0) != seed:
        raise ValueError("resume source seed does not match")
    messages = source_checkpoint.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("resume checkpoint has no worker conversation")

    manifest_path = source_attempt / "solve_manifest.json"
    if manifest_path.is_file():
        source_manifest = _read_json(manifest_path)
        if source_manifest.get("termination_reason") not in {
            "main_worker_provider_failure",
            "reporter_provider_failure",
            "controller_provider_failure",
        }:
            raise ValueError("resume source is not a provider-interrupted attempt")
    else:
        attempt_path = source_attempt / "attempt_state.json"
        source_attempt_state = _read_json(attempt_path)
        if source_attempt_state.get("status") != "interrupted":
            raise ValueError(
                "resume source has neither a provider failure nor an interrupted attempt receipt"
            )

    source_workspace = source_attempt.parent / f"{source_attempt.name}.workspace"
    if not source_workspace.is_dir():
        raise ValueError("resume source workspace is missing")

    out_dir.mkdir(parents=True, exist_ok=False)
    resume_workspace = out_dir.parent / f"{out_dir.name}.workspace"
    if resume_workspace.exists() or resume_workspace.is_symlink():
        raise ValueError("resume workspace path already exists")
    resume_workspace.symlink_to(source_workspace.resolve(), target_is_directory=True)
    if "journal_sequence" in source_checkpoint:
        source_journal = source_attempt / "in_progress_journal.jsonl"
        journal_lines = source_journal.read_text(encoding="utf-8").splitlines()
        boundary = source_checkpoint["journal_sequence"]
        if type(boundary) is not int or not 0 <= boundary <= len(journal_lines):
            raise ValueError("resume checkpoint journal boundary is invalid")
        (out_dir / "in_progress_journal.jsonl").write_text(
            "".join(f"{line}\n" for line in journal_lines[:boundary]), encoding="utf-8"
        )
    shutil.copyfile(checkpoint_path, out_dir / "recovery_checkpoint.json")
    atomic_write_json(
        out_dir / "resumed_from.json",
        {

            "source_attempt": str(source_attempt),
            "source_worker_turns": int(source_checkpoint.get("main_worker_turns") or 0),
            "source_cycle_index": int(source_checkpoint.get("cycle_index") or 0),
            "resume_boundary": str(source_checkpoint.get("phase") or "call_worker"),
        },
    )
    return resume_workspace


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.preflight_only:
        if not args.worker_model or not args.worker_model.strip():
            parser.error("--worker-model is required for model execution")
        if args.arm == "controlled" and args.controller_provider == "model":
            if not args.controller_model or not args.controller_model.strip():
                parser.error("--controller-model is required for a model Controller")
    if args.preflight_only:
        if args.assets_root is None:
            raise SystemExit("--assets-root or LOOPARENA_ASSETS_ROOT is required")
        result = preflight_type2_case(
            args.case_dir,
            assets_root=args.assets_root,
            scbench_runtime_profile=args.scbench_runtime_profile,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.controller_provider != "model" and args.arm != "controlled":
        raise SystemExit("non-adaptive fixed control is controlled-only")
    case_dir = args.case_dir.expanduser().resolve()
    manifest = _case_manifest(case_dir)
    prefix_errors = validate_public_conversation(
        _read_json(case_dir / "public_messages.json")
    )
    if prefix_errors:
        raise SystemExit(
            "public conversation cannot be replayed: " + "; ".join(prefix_errors)
        )
    if args.assets_root is None:
        raise SystemExit("set LOOPARENA_ASSETS_ROOT or pass --assets-root")
    assets_root = args.assets_root.expanduser().resolve()
    if not assets_root.is_dir():
        raise SystemExit(f"assets root is not a directory: {assets_root}")

    stored_plan = _read_json(case_dir / "evaluator_plan.json")
    scbench_profile = None
    harbor_profile = None
    loopsbench_profile = None
    if stored_plan.get("adapter_kind") == "scbench":
        scbench_profile = _scbench_runtime_profile(
            stored_plan,
            case_dir,
            args.scbench_runtime_profile,
        )
    elif stored_plan.get("adapter_kind") == "beyondswe_harbor":
        if args.scbench_runtime_profile != "canonical-amd64":
            raise SystemExit("--scbench-runtime-profile applies only to SCBench cases")
        try:
            harbor_profile = _harbor_image_profile(
                stored_plan,
                case_dir,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    elif stored_plan.get("adapter_kind") == "loopsbench":
        if args.scbench_runtime_profile != "canonical-amd64":
            raise SystemExit("--scbench-runtime-profile applies only to SCBench cases")
        try:
            loopsbench_profile = _loopsbench_image_profile(stored_plan, case_dir)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.scbench_runtime_profile != "canonical-amd64":
        raise SystemExit("--scbench-runtime-profile applies only to SCBench cases")
    resolved_image_id: str | None = None
    if stored_plan.get("adapter_kind") == "scbench":
        try:
            resolved_image_id = _ensure_scbench_image(
                stored_plan,
                assets_root,
                scbench_profile,
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    elif stored_plan.get("adapter_kind") == "beyondswe_harbor":
        try:
            resolved_image_id = _ensure_harbor_image(
                stored_plan,
                assets_root,
                harbor_profile,
            )
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    elif stored_plan.get("adapter_kind") == "loopsbench":
        try:
            resolved_image_id = _ensure_loopsbench_image(
                stored_plan,
                assets_root,
                loopsbench_profile,
            )
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    evaluator_plan = localize_evaluator_plan(
        stored_plan,
        assets_root=assets_root,
    )
    if resolved_image_id is not None:
        runtime = evaluator_plan["runtime_identity"]
        if evaluator_plan.get("adapter_kind") == "scbench":
            runtime["evaluation_image_id"] = resolved_image_id
        else:
            runtime["image_id"] = resolved_image_id
    (
        solve_image,
        mount_point,
        solve_network,
        solve_setup_commands,
    ) = _solve_runtime(evaluator_plan)
    try:
        _require_evaluator_assets(evaluator_plan)
        workspace_archive = _workspace_archive(case_dir, assets_root)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    solve_cpus, solve_memory_mb = _source_solve_limits(evaluator_plan)

    out_dir = args.out_dir.expanduser().resolve()
    if out_dir.exists():
        raise SystemExit(f"out dir already exists: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.resume_from_attempt is not None:
        if args.workspace_dir is not None:
            raise SystemExit(
                "--workspace-dir cannot be combined with --resume-from-attempt"
            )
        try:
            workspace = _prepare_provider_resume(
                source_attempt=args.resume_from_attempt,
                out_dir=out_dir,
                arm=args.arm,
                sample_id=manifest["sample_id"],
                seed=args.seed,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"cannot resume provider-interrupted attempt: {exc}"
            ) from exc
    else:
        workspace = (
            args.workspace_dir.expanduser().resolve()
            if args.workspace_dir is not None
            else out_dir.parent / f"{out_dir.name}.workspace"
        )
        if workspace.exists():
            raise SystemExit(f"workspace dir already exists: {workspace}")
        safe_extract_workspace_archive(
            workspace_archive,
            workspace,
        )
        normalize_workspace_archive_root(workspace)
        _restore_beyondswe_git_metadata(evaluator_plan, workspace)
        sanitize_workspace_git_history(workspace)
        _materialize_scbench_public_static_assets(evaluator_plan, workspace)

    with tempfile.TemporaryDirectory(
        prefix=f"{case_dir.name}-evaluator-",
        dir=out_dir.parent,
    ) as temporary:
        localized_plan = Path(temporary) / "evaluator_plan.json"
        localized_plan.write_text(
            json.dumps(evaluator_plan, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "looparena.commands.harness",
            "--arm",
            args.arm,
            "--task-file",
            str(case_dir / "task.txt"),
            "--workspace",
            str(workspace),
            "--out-dir",
            str(out_dir),
            "--sample-id",
            manifest["sample_id"],
            "--seed",
            str(args.seed),
            "--worker-model",
            args.worker_model,
            "--controller-provider",
            args.controller_provider,
            "--credential-profile-id",
            args.credential_profile_id,
            "--controller-api-key-env",
            args.controller_api_key_env,
            "--image",
            solve_image,
            "--mount-point",
            mount_point,
            "--network",
            solve_network,
            "--worker-wall-time-sec",
            str(args.worker_wall_time_sec),
            "--reporter-wall-time-sec",
            str(args.reporter_wall_time_sec),
            "--gateway-timeout-sec",
            str(args.gateway_timeout_sec),
            "--tool-timeout-sec",
            str(args.tool_timeout_sec),
            "--evaluator-plan",
            str(localized_plan),
        ]
        if args.resume_from_attempt is not None:
            command.append("--resume-run")
        else:
            command.extend(
                [
                    "--serialized-messages",
                    str(case_dir / "public_messages.json"),
                ]
            )
        if solve_cpus is not None:
            command.extend(["--cpus", str(solve_cpus)])
        if solve_memory_mb is not None:
            command.extend(["--memory-mb", str(solve_memory_mb)])
        if solve_setup_commands:
            if stored_plan.get("adapter_kind") == "scbench":
                command.extend(["--solve-setup-timeout-sec", "310"])
            for setup_command in solve_setup_commands:
                command.extend(["--solve-setup-command", setup_command])
        if stored_plan.get("adapter_kind") == "loopsbench":
            command.append("--normalize-solve-workspace-permissions-after-stop")
        command.extend(["--api-key-env", args.api_key_env])
        if args.arm == "controlled" and args.controller_provider == "model":
            command.extend(["--controller-model", args.controller_model])
        if args.base_url:
            command.extend(["--base-url", args.base_url])
        if args.controller_base_url:
            command.extend(["--controller-base-url", args.controller_base_url])
        if args.controller_credential_profile_id:
            command.extend(
                [
                    "--controller-credential-profile-id",
                    args.controller_credential_profile_id,
                ]
            )
        run_environment = os.environ.copy()
        if stored_plan.get("adapter_kind") == "scbench":
            platform_name = str(
                (evaluator_plan.get("runtime_identity") or {}).get(
                    "evaluation_image_platform"
                )
                or "linux/amd64"
            ).replace("/", "-")
            dependency_root = (
                assets_root / "scbench_runtime_cache" / f"{platform_name}-py312"
            )
            uv_cache = dependency_root / "uv-cache"
            uv_cache.mkdir(parents=True, exist_ok=True)
            run_environment["LOOPARENA_SCBENCH_UV_CACHE_DIR"] = str(uv_cache)
        return subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            env=run_environment,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
