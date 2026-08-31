from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
TYPE2 = ROOT / "benchmarks" / "type2"
TYPE3 = ROOT / "benchmarks" / "type3"
REQUIRED_CASE_FILES = {
    "case.json",
    "task.txt",
    "public_messages.json",
    "workspace.tar.gz",
    "evaluator_plan.json",
    "provenance.json",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_type2_release_is_complete_and_paired_with_type3() -> None:
    index = read_json(TYPE2 / "CASE_INDEX.json")
    type3 = read_json(TYPE3 / "CASE_INDEX.json")
    type3_cases = {row["case_id"]: row for row in type3["cases"]}

    assert index["schema_version"] == "looparena.type2_case_index.v1"
    assert index["counts"] == {
        "total": 27,
        "unique_official_tasks": 27,
        "scbench": 11,
        "beyondswe_harbor": 16,
    }
    assert len(index["cases"]) == 27
    assert {row["source_type3_case_id"] for row in index["cases"]} == set(type3_cases)

    for row in index["cases"]:
        paired = type3_cases[row["source_type3_case_id"]]
        assert row["official_task_id"] == paired["official_task_id"]
        assert row["adapter_kind"] == paired["adapter_kind"]

        case_dir = TYPE2 / "cases" / row["case_id"]
        assert {path.name for path in case_dir.iterdir()} == REQUIRED_CASE_FILES
        case = read_json(case_dir / "case.json")
        plan = read_json(case_dir / "evaluator_plan.json")
        provenance = read_json(case_dir / "provenance.json")
        messages = read_json(case_dir / "public_messages.json")

        assert case == {
            "case_id": row["case_id"],
            "sample_id": case["sample_id"],
            "schema_version": "looparena.type2_case.v1",
            "start_mode": "bootstrap_contract_start",
        }
        assert plan["schema_version"] == "looparena.terminal_evaluator_plan.v1"
        if row["adapter_kind"] == "beyondswe_harbor":
            paired_plan = read_json(
                TYPE3
                / "cases"
                / row["source_type3_case_id"]
                / "evaluator_plans"
                / "task.json"
            )
            image_id = plan["runtime_identity"]["image_id"]
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
            assert paired_plan["runtime_identity"]["image_id"] == image_id
        assert [message["role"] for message in messages] == ["system", "user"]

        archive = case_dir / "workspace.tar.gz"
        expected = provenance.get("workspace_archive_sha256") or provenance.get(
            "workspace", {}
        ).get("sha256")
        assert expected.removeprefix("sha256:") == sha256_file(archive)
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                path = PurePosixPath(member.name)
                assert not path.is_absolute() and ".." not in path.parts
                assert ".git" not in path.parts
                if member.issym() or member.islnk():
                    target = PurePosixPath(member.linkname)
                    assert not target.is_absolute() and ".." not in target.parts


def test_case039_contains_its_pinned_llhttp_submodule() -> None:
    archive = TYPE2 / "cases" / "beyondswe-case039-cp1" / "workspace.tar.gz"
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert ".gitmodules" in names
    assert "vendor/llhttp/src/native/api.c" in names
