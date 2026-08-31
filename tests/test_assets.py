from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from looparena.commands.assets import (
    BEYONDSWE_DIR,
    SCBENCH_PROBLEMS_DIR,
    SCBENCH_RUNNER_DIR,
    TYPE2_ASSET_RECEIPT,
    TYPE2_BEYONDSWE_DIR,
    _link,
    _prepare_beyondswe,
    _prepare_scbench,
    _prepare_type2_beyondswe,
    prepare,
)

ROOT = Path(__file__).resolve().parents[1]


def test_public_upstreams_are_pinned() -> None:
    settings = tomllib.loads(
        (ROOT / "benchmarks/upstreams.toml").read_text(encoding="utf-8")
    )
    assert settings["beyondswe"]["revision"] == (
        "7d2ced21b4c85f646d5ba8786875d7fbbe08ed49"
    )
    assert settings["scbench"]["runner_revision"] == (
        "13de1a7a6b8b3dc5cc532a0c322a0997afa5bec7"
    )
    assert settings["scbench"]["problems_repository"] == (
        "https://github.com/gabeorlanski/scb-problems.git"
    )
    assert settings["scbench"]["problems_revision"] == (
        "4d38d300059667d57e43c31969bc455f5c338b52"
    )


def test_asset_layout_is_plain_and_shared_by_all_plans() -> None:
    expected_roots = {
        f"/opt/looparena/evaluator_assets/{BEYONDSWE_DIR}",
        f"/opt/looparena/evaluator_assets/{SCBENCH_PROBLEMS_DIR}",
        f"/opt/looparena/evaluator_assets/{SCBENCH_RUNNER_DIR}",
        f"/opt/looparena/evaluator_assets/{TYPE2_BEYONDSWE_DIR}",
    }
    observed: set[str] = set()
    plan_paths = [
        *(ROOT / "benchmarks/type2/cases").glob("*/evaluator_plan.json"),
        *(ROOT / "benchmarks/type3/cases").glob("*/evaluator_plans/*.json"),
    ]
    for plan_path in plan_paths:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        config = plan.get("adapter_config") or {}
        for field in ("task_dir", "problems_root", "runner_root"):
            value = config.get(field)
            if value:
                observed.add(
                    next(root for root in expected_roots if str(value).startswith(root))
                )
    assert observed == expected_roots


def test_beyondswe_assets_link_to_user_download(tmp_path: Path) -> None:
    registry = json.loads(
        (ROOT / "benchmarks/type3/BEYONDSWE_OFFICIAL_EVALUATORS.json").read_text()
    )
    source = tmp_path / "download" / "beyondswe"
    for record in registry["cases"].values():
        slug = record["official_task_id"].split(":", 1)[1]
        task = source / slug
        task.mkdir(parents=True)
        (task / "task.toml").write_text("version = '1.0'\n", encoding="utf-8")

    assets = tmp_path / "assets"
    _prepare_beyondswe(root=ROOT, assets_root=assets, source_root=source.parent)
    links = list((assets / BEYONDSWE_DIR).glob("*/task"))
    assert len(links) == 16
    assert all(link.is_symlink() for link in links)


def test_scbench_assets_link_to_user_download(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    case = root / "benchmarks/type3/cases/example"
    (case / "evaluator_plans").mkdir(parents=True)
    (case / "evaluator_plans/checkpoint_1.json").write_text(
        json.dumps(
            {
                "adapter_kind": "scbench",
                "adapter_config": {"problem_name": "example"},
            }
        ),
        encoding="utf-8",
    )
    source = tmp_path / "download/scb-problems/example"
    (source / "tests").mkdir(parents=True)
    (source / "config.yaml").write_text("version: 2\n", encoding="utf-8")
    (source / "tests/test_example.py").write_text("def test_ok(): pass\n")

    assets = tmp_path / "assets"
    assets.mkdir()
    _prepare_scbench(
        root=root,
        assets_root=assets,
        source_root=source.parent,
    )
    prepared = assets / SCBENCH_PROBLEMS_DIR
    assert prepared.is_symlink()
    assert (prepared / "example/config.yaml").read_text() == "version: 2\n"


def test_existing_asset_link_must_match_requested_source(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    destination = tmp_path / "assets/source"
    _link(first, destination)
    _link(first, destination)
    with pytest.raises(FileExistsError, match="different source"):
        _link(second, destination)


def test_prepare_validates_selected_sources_before_creating_assets(
    tmp_path: Path,
) -> None:
    assets = tmp_path / "assets"
    with pytest.raises(FileNotFoundError, match="missing BeyondSWE task"):
        prepare(
            assets_root=assets,
            beyondswe_source=tmp_path / "missing-beyondswe",
            scbench_runner=None,
            scbench_source=None,
            only="beyondswe",
        )
    assert not assets.exists()


def test_type2_beyondswe_evaluators_are_built_from_upstream(tmp_path: Path) -> None:
    registry = json.loads(
        (ROOT / "benchmarks/type3/BEYONDSWE_OFFICIAL_EVALUATORS.json").read_text()
    )["cases"]
    recipes = ROOT / "benchmarks/type2/evaluator_recipes"
    source_root = tmp_path / "download/beyondswe"
    original_patches = {}

    for tests_path in recipes.glob("*.txt"):
        case_dir = ROOT / "benchmarks/type2/cases" / tests_path.stem
        provenance = json.loads((case_dir / "provenance.json").read_text())
        source_case = provenance["source_case_id"]
        slug = registry[source_case]["official_task_id"].split(":", 1)[1]
        source = source_root / slug
        tests = source / "tests"
        tests.mkdir(parents=True)
        (source / "task.toml").write_text("version = '1.0'\n")
        selected = tests_path.read_text().splitlines()
        (tests / "test_config.json").write_text(
            json.dumps(
                {
                    "instance_id": slug,
                    "fail_to_pass": [*selected, "not-selected"],
                    "pass_to_pass": ["stable"],
                },
                indent=2,
            )
            + "\n"
        )
        original_patches[tests_path.stem] = f"official patch for {slug}\n"
        (tests / "f2p_patch.diff").write_text(original_patches[tests_path.stem])
        (tests / "instance.json").write_text(
            json.dumps(
                {
                    "f2p_patch": original_patches[tests_path.stem],
                    "untouched": True,
                },
                indent=2,
            )
        )

    assets = tmp_path / "assets"
    _prepare_type2_beyondswe(
        root=ROOT,
        assets_root=assets,
        source_root=source_root.parent,
    )

    for tests_path in recipes.glob("*.txt"):
        case_dir = ROOT / "benchmarks/type2/cases" / tests_path.stem
        provenance = json.loads((case_dir / "provenance.json").read_text())
        destination = (
            assets
            / TYPE2_BEYONDSWE_DIR
            / provenance["source_case_id"]
            / provenance["source_checkpoint_id"]
        )
        config = json.loads((destination / "tests/test_config.json").read_text())
        assert config["fail_to_pass"] == tests_path.read_text().splitlines()
        assert config["pass_to_pass"] == ["stable"]

        fixture = tests_path.with_suffix(".patch")
        expected_patch = original_patches[tests_path.stem]
        if fixture.is_file():
            expected_patch = fixture.read_text() + expected_patch
        actual_patch = (destination / "tests/f2p_patch.diff").read_text()
        instance = json.loads((destination / "tests/instance.json").read_text())
        assert actual_patch == expected_patch
        assert instance == {"f2p_patch": expected_patch, "untouched": True}
        receipt = json.loads((destination / TYPE2_ASSET_RECEIPT).read_text())
        assert receipt["schema_version"] == 1
        assert len(receipt["source_evaluator_sha256"]) == 64
        assert len(receipt["recipe_sha256"]) == 64
        assert len(receipt["prepared_evaluator_sha256"]) == 64

    changed_recipe = next(recipes.glob("*.txt"))
    changed_case = ROOT / "benchmarks/type2/cases" / changed_recipe.stem
    provenance = json.loads((changed_case / "provenance.json").read_text())
    receipt = (
        assets
        / TYPE2_BEYONDSWE_DIR
        / provenance["source_case_id"]
        / provenance["source_checkpoint_id"]
        / TYPE2_ASSET_RECEIPT
    )
    receipt.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="does not match"):
        _prepare_type2_beyondswe(
            root=ROOT,
            assets_root=assets,
            source_root=source_root.parent,
        )

    _prepare_type2_beyondswe(
        root=ROOT,
        assets_root=tmp_path / "clean-assets",
        source_root=source_root.parent,
    )
    clean_destination = (
        tmp_path
        / "clean-assets"
        / TYPE2_BEYONDSWE_DIR
        / provenance["source_case_id"]
        / provenance["source_checkpoint_id"]
    )
    (clean_destination / "tests/test_config.json").write_text("{}\n")
    with pytest.raises(FileExistsError, match="does not match"):
        _prepare_type2_beyondswe(
            root=ROOT,
            assets_root=tmp_path / "clean-assets",
            source_root=source_root.parent,
        )
