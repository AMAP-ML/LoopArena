"""Arrange user-downloaded upstream assets for LoopArena."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from looparena.paths import repository_root

SCBENCH_RUNNER_DIR = "scbench/runner"
SCBENCH_PROBLEMS_DIR = "scbench/problems"
BEYONDSWE_DIR = "beyondswe/tasks"
TYPE2_BEYONDSWE_DIR = "type2/beyondswe"
TYPE2_ASSET_RECEIPT = ".looparena-asset.json"


def _beyondswe_slugs(root: Path) -> list[str]:
    registry = json.loads(
        (root / "benchmarks/type3/BEYONDSWE_OFFICIAL_EVALUATORS.json").read_text(
            encoding="utf-8"
        )
    )
    return sorted(
        value["official_task_id"].split(":", 1)[1]
        for value in registry["cases"].values()
    )


def _beyondswe_source_root(source_root: Path) -> Path:
    source_root = source_root.expanduser().resolve()
    nested = source_root / "beyondswe"
    return nested if nested.is_dir() else source_root


def _scbench_problems(root: Path) -> list[str]:
    problems = set()
    for case_dir in (root / "benchmarks/type3/cases").iterdir():
        for plan_path in (case_dir / "evaluator_plans").glob("*.json"):
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if plan.get("adapter_kind") == "scbench":
                problems.add(plan["adapter_config"]["problem_name"])
                break
    return sorted(problems)


def _link(source: Path, destination: Path) -> None:
    expected = source.resolve()
    if destination.is_symlink():
        if destination.resolve(strict=False) != expected:
            raise FileExistsError(
                f"asset link points to a different source: {destination}"
            )
        print(f"reuse {destination}")
        return
    if destination.exists():
        raise FileExistsError(f"asset path exists and is not a symlink: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(expected, target_is_directory=True)
    print(f"linked {destination} -> {expected}")


def _type2_asset_identity(source: Path, recipe: Path) -> dict[str, str | int]:
    digest = hashlib.sha256()
    for relative in (
        "task.toml",
        "tests/test.sh",
        "tests/test_config.json",
        "tests/f2p_patch.diff",
        "tests/instance.json",
    ):
        path = source / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    recipe_digest = hashlib.sha256()
    recipe_digest.update(b"recipe\0")
    recipe_digest.update(recipe.read_bytes())
    fixture = recipe.with_suffix(".patch")
    if fixture.is_file():
        recipe_digest.update(b"\0fixture\0")
        recipe_digest.update(fixture.read_bytes())
    return {
        "schema_version": 1,
        "source_evaluator_sha256": digest.hexdigest(),
        "recipe_sha256": recipe_digest.hexdigest(),
    }


def _prepared_type2_asset_digest(destination: Path) -> str:
    digest = hashlib.sha256()
    for relative in (
        "task.toml",
        "tests/test.sh",
        "tests/test_config.json",
        "tests/f2p_patch.diff",
        "tests/instance.json",
    ):
        path = destination / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_type2_asset(destination: Path, expected: dict[str, str | int]) -> None:
    receipt_path = destination / TYPE2_ASSET_RECEIPT
    try:
        observed = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileExistsError(
            f"existing Type II asset has no valid identity receipt: {destination}"
        ) from exc
    observed_inputs = {key: observed.get(key) for key in expected}
    if observed_inputs != expected or observed.get(
        "prepared_evaluator_sha256"
    ) != _prepared_type2_asset_digest(destination):
        raise FileExistsError(
            f"existing Type II asset does not match the current source and recipe: "
            f"{destination}"
        )


def _prepare_beyondswe(*, root: Path, assets_root: Path, source_root: Path) -> None:
    source_root = _beyondswe_source_root(source_root)
    for slug in _beyondswe_slugs(root):
        source = source_root / slug
        if not (source / "task.toml").is_file():
            raise FileNotFoundError(f"missing BeyondSWE task: {source}")
        _link(source, assets_root / BEYONDSWE_DIR / slug / "task")


def _prepare_type2_beyondswe(
    *, root: Path, assets_root: Path, source_root: Path
) -> None:
    source_root = _beyondswe_source_root(source_root)
    registry = json.loads(
        (root / "benchmarks/type3/BEYONDSWE_OFFICIAL_EVALUATORS.json").read_text(
            encoding="utf-8"
        )
    )["cases"]
    recipes = root / "benchmarks/type2/evaluator_recipes"

    for tests_path in sorted(recipes.glob("*.txt")):
        case_dir = root / "benchmarks/type2/cases" / tests_path.stem
        provenance = json.loads(
            (case_dir / "provenance.json").read_text(encoding="utf-8")
        )
        source_case = provenance["source_case_id"]
        checkpoint = provenance["source_checkpoint_id"]
        slug = registry[source_case]["official_task_id"].split(":", 1)[1]
        destination = assets_root / TYPE2_BEYONDSWE_DIR / source_case / checkpoint
        source = source_root / slug
        identity = _type2_asset_identity(source, tests_path)

        if destination.exists():
            _validate_type2_asset(destination, identity)
            print(f"reuse {destination}")
            continue

        shutil.copytree(source, destination)
        tests = destination / "tests"
        config_path = tests / "test_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["fail_to_pass"] = tests_path.read_text(encoding="utf-8").splitlines()
        config_path.write_text(
            json.dumps(config, indent=2) + "\n",
            encoding="utf-8",
        )

        fixture_path = tests_path.with_suffix(".patch")
        if fixture_path.is_file():
            patch_path = tests / "f2p_patch.diff"
            patch = fixture_path.read_text(encoding="utf-8") + patch_path.read_text(
                encoding="utf-8"
            )
            patch_path.write_text(patch, encoding="utf-8")
            instance_path = tests / "instance.json"
            instance = json.loads(instance_path.read_text(encoding="utf-8"))
            instance["f2p_patch"] = patch
            instance_path.write_text(
                json.dumps(instance, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        receipt = {
            **identity,
            "prepared_evaluator_sha256": _prepared_type2_asset_digest(destination),
        }
        (destination / TYPE2_ASSET_RECEIPT).write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        print(f"prepared {destination}")


def _prepare_scbench(*, root: Path, assets_root: Path, source_root: Path) -> None:
    source_root = source_root.expanduser().resolve()
    for problem in _scbench_problems(root):
        source = source_root / problem
        if not (source / "config.yaml").is_file() or not (source / "tests").is_dir():
            raise FileNotFoundError(f"missing SCBench problem: {source}")
    _link(source_root, assets_root / SCBENCH_PROBLEMS_DIR)


def _validate_beyondswe_source(*, root: Path, source_root: Path) -> Path:
    source_root = _beyondswe_source_root(source_root)
    missing = [
        source_root / slug / "task.toml"
        for slug in _beyondswe_slugs(root)
        if not (source_root / slug / "task.toml").is_file()
    ]
    if missing:
        raise FileNotFoundError(f"missing BeyondSWE task: {missing[0].parent}")
    return source_root


def _validate_scbench_sources(
    *, root: Path, runner: Path, source_root: Path
) -> tuple[Path, Path]:
    runner = runner.expanduser().resolve()
    source_root = source_root.expanduser().resolve()
    if not (runner / "pyproject.toml").is_file():
        raise FileNotFoundError(f"missing SCBench runner: {runner}")
    for problem in _scbench_problems(root):
        source = source_root / problem
        if not (source / "config.yaml").is_file() or not (source / "tests").is_dir():
            raise FileNotFoundError(f"missing SCBench problem: {source}")
    return runner, source_root


def prepare(
    *,
    assets_root: Path,
    beyondswe_source: Path | None,
    scbench_runner: Path | None,
    scbench_source: Path | None,
    only: str = "all",
) -> None:
    root = repository_root()
    if only not in {"all", "beyondswe", "scbench"}:
        raise ValueError(f"unsupported asset selection: {only}")
    prepare_beyondswe = only in {"all", "beyondswe"}
    prepare_scbench = only in {"all", "scbench"}

    normalized_beyondswe: Path | None = None
    normalized_runner: Path | None = None
    normalized_scbench: Path | None = None
    if prepare_beyondswe:
        if beyondswe_source is None:
            raise ValueError("--beyondswe-source is required for BeyondSWE assets")
        normalized_beyondswe = _validate_beyondswe_source(
            root=root,
            source_root=beyondswe_source,
        )
    if prepare_scbench:
        if scbench_runner is None or scbench_source is None:
            raise ValueError(
                "--scbench-runner and --scbench-source are required for SCBench assets"
            )
        normalized_runner, normalized_scbench = _validate_scbench_sources(
            root=root,
            runner=scbench_runner,
            source_root=scbench_source,
        )

    # Validate every selected upstream before creating or linking any assets.
    assets_root = assets_root.expanduser().resolve()
    assets_root.mkdir(parents=True, exist_ok=True)
    if normalized_beyondswe is not None:
        _prepare_beyondswe(
            root=root,
            assets_root=assets_root,
            source_root=normalized_beyondswe,
        )
        _prepare_type2_beyondswe(
            root=root,
            assets_root=assets_root,
            source_root=normalized_beyondswe,
        )
    if normalized_runner is not None and normalized_scbench is not None:
        _link(normalized_runner, assets_root / SCBENCH_RUNNER_DIR)
        _prepare_scbench(
            root=root,
            assets_root=assets_root,
            source_root=normalized_scbench,
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Arrange user-downloaded upstream assets for LoopArena."
    )
    subcommands = result.add_subparsers(dest="command", required=True)
    prepare_parser = subcommands.add_parser("prepare")
    prepare_parser.add_argument(
        "--assets-root",
        type=Path,
        default=Path(
            os.environ.get("LOOPARENA_ASSETS_ROOT", "~/.cache/looparena/assets")
        ),
    )
    prepare_parser.add_argument(
        "--only",
        choices=("all", "beyondswe", "scbench"),
        default="all",
        help="Prepare all assets or only one upstream family.",
    )
    prepare_parser.add_argument("--beyondswe-source", type=Path)
    prepare_parser.add_argument("--scbench-runner", type=Path)
    prepare_parser.add_argument("--scbench-source", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    try:
        prepare(
            assets_root=args.assets_root,
            beyondswe_source=args.beyondswe_source,
            scbench_runner=args.scbench_runner,
            scbench_source=args.scbench_source,
            only=args.only,
        )
    except (FileExistsError, FileNotFoundError, OSError, ValueError) as exc:
        command_parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
