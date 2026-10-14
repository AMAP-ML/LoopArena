"""Prepare the 80-task asset layout from pinned, user-downloaded upstream sources."""

from __future__ import annotations
import argparse, json, shutil, subprocess, tarfile, tempfile
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path
from looparena.paths import repository_root
from looparena.commands.workspaces import safe, sha
from looparena.evaluators.loopsbench import task_tree_sha256

PREFIX = "/opt/looparena/evaluator_assets/"


def load(p):
    return json.loads(p.read_text())


def plans(root):
    paths = list(
        (root / "benchmarks/type2/cases").glob("*/evaluator_plan.json")
    ) + list((root / "benchmarks/type3/cases").glob("*/evaluator_plans/*.json"))
    return [load(p) for p in paths if load(p).get("adapter_kind")]


def link(source, target):
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if target.is_symlink() and target.resolve() == source:
        return
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=source.is_dir())


def prepare_beyondswe(root, source, assets):
    config = root / "benchmarks/adaptations/beyondswe"
    spec = load(config / "recipes.json")
    archive = config / "overlays.tar.gz"
    if sha(archive.read_bytes()) != spec["overlay_sha256"]:
        raise ValueError("evaluator overlay identity changed")
    blobs = {}
    with tarfile.open(archive) as tar:
        for f in tar:
            if not f.isfile():
                raise ValueError("invalid evaluator overlay entry")
            data = tar.extractfile(f).read()
            if sha(data) != f.name or f.name in blobs:
                raise ValueError("invalid evaluator overlay content")
            blobs[f.name] = data
    for r in spec["recipes"]:
        destination = safe(assets, r["target"])
        expected = {x["path"] for x in r["files"]}
        if destination.exists():
            actual = {
                p.relative_to(destination).as_posix() for p in destination.rglob("*")
            }
            if actual != expected:
                raise ValueError(
                    f"evaluator directory differs from frozen recipe: {r['target']}"
                )
            for x in r["files"]:
                p = safe(destination, x["path"])
                if x["kind"] == "file" and (
                    not p.is_file() or sha(p.read_bytes()) != x["sha256"]
                ):
                    raise ValueError("existing evaluator content mismatch")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".prepare-", dir=destination.parent
        ) as temporary:
            prepared = Path(temporary) / "task"
            prepared.mkdir()
            for x in r["files"]:
                p = safe(prepared, x["path"])
                if x["kind"] == "directory":
                    p.mkdir(parents=True, exist_ok=True)
                    continue
                spec = x["source"]
                data = (
                    blobs[spec["blob"]]
                    if spec["kind"] == "overlay"
                    else safe(source, spec["path"]).read_bytes()
                )
                if len(data) != x["bytes"] or sha(data) != x["sha256"]:
                    raise ValueError(f"upstream evaluator file changed: {x['path']}")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                p.chmod(x["mode"])
            prepared.rename(destination)


def prepare_loopsbench(root, source, assets):
    config = root / "benchmarks/adaptations/loopsbench"
    for r in load(config / "recipes.json")["recipes"]:
        source_task = safe(source, "tasks/" + r["task"])
        target = safe(assets, "tasks/" + r["task"])
        if target.exists():
            if task_tree_sha256(target) != r["prepared_tree_sha256"]:
                raise ValueError("existing LoopsBench evaluator tree changed")
            continue
        if task_tree_sha256(source_task) != r["upstream_tree_sha256"]:
            raise ValueError(f"wrong pinned LoopsBench task: {r['task']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".prepare-", dir=target.parent
        ) as temporary:
            prepared = Path(temporary) / "task"
            shutil.copytree(source_task, prepared, symlinks=True)
            for rel in r["removed"]:
                p = safe(prepared, rel)
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
            for x in r["changed"]:
                data = safe(config, x["file"]).read_bytes()
                if sha(data) != x["sha256"]:
                    raise ValueError("LoopsBench adapter changed")
                p = safe(prepared, x["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
                p.chmod(x["mode"])
            if task_tree_sha256(prepared) != r["prepared_tree_sha256"]:
                raise ValueError("prepared LoopsBench identity mismatch")
            prepared.rename(target)


def _validate_scbench_sources(
    *, root: Path, runner: Path, source_root: Path
) -> tuple[Path, Path]:
    runner = runner.expanduser().resolve()
    source_root = source_root.expanduser().resolve()
    if not (runner / "pyproject.toml").is_file():
        raise FileNotFoundError(f"missing SCBench runner: {runner}")
    revision = tomllib.loads((root / "benchmarks/upstreams.toml").read_text())["scbench"]["runner_revision"]
    def git(*args):
        return subprocess.run(["git", "-C", str(runner), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    if git("rev-parse", "HEAD") != revision or git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("SCBench runner must be a clean checkout of the pinned revision")
    validate_scbench_problems(root, source_root)
    return runner, source_root


def validate_scbench_problems(root: Path, source: Path, selected: str | None = None) -> None:
    inventory = load(root / "benchmarks/adaptations/scbench/source-files.json")
    problems = [p for p in inventory["problems"] if selected is None or p["problem"] == selected]
    if not problems:
        raise ValueError("SCBench problem is absent from the frozen inventory")
    for problem in problems:
        base = safe(source, problem["problem"])
        expected = {entry["path"] for entry in problem["files"]}
        actual = {
            path.relative_to(base).as_posix() for path in base.rglob("*") if path.is_file()
            and not ("__pycache__" in path.relative_to(base).parts and path.suffix == ".pyc")
            and ".pytest_cache" not in path.relative_to(base).parts
        }
        if actual != expected:
            raise ValueError(f"SCBench source file set differs: {problem['problem']}")
        for entry in problem["files"]:
            path = safe(base, entry["path"])
            if not path.is_file() or sha(path.read_bytes()) != entry["sha256"]:
                raise ValueError(f"SCBench source content differs: {problem['problem']}/{entry['path']}")


def prepare_scbench(root, runner, source, assets):
    runner, source = _validate_scbench_sources(root=root, runner=runner, source_root=source)
    links = {}
    for plan in plans(root):
        if plan["adapter_kind"] != "scbench":
            continue
        c = plan["adapter_config"]
        problem = c["problem_name"]
        if not (source / problem / "config.yaml").is_file():
            raise ValueError(f"missing SCBench problem: {problem}")
        for field, upstream in [("runner_root", runner), ("problems_root", source)]:
            rel = c[field].removeprefix(PREFIX)
            links[rel] = upstream
        if c.get("private_bundle_path"):
            rel = c["private_bundle_path"].removeprefix(PREFIX)
            if rel != c["problems_root"].removeprefix(PREFIX) + "/" + problem:
                links[rel] = source / problem
    for rel, upstream in sorted(links.items()):
        # Existing intentional source links may resolve outside assets_root.
        target = assets / rel
        if target.is_symlink() and target.resolve() == upstream.resolve():
            continue
        link(upstream, safe(assets, rel))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assets-root", type=Path, required=True)
    p.add_argument(
        "--only", choices=["all", "beyondswe", "scbench", "loopsbench"], default="all"
    )
    p.add_argument("--beyondswe-source", type=Path)
    p.add_argument("--scbench-runner", type=Path)
    p.add_argument("--scbench-source", type=Path)
    p.add_argument("--loopsbench-source", type=Path)
    p.add_argument(
        "--loopsbench-runtime",
        choices=["none", "dependencies", "images", "all"],
        default="none",
        help="Explicitly download pinned runtime dependencies and/or build source Docker images.",
    )
    p.add_argument(
        "--loopsbench-task",
        action="append",
        help="Build only this LoopsBench task's image; may repeat.",
    )
    a = p.parse_args(argv)
    if a.loopsbench_runtime != "none" and a.only not in {"all", "loopsbench"}:
        p.error("--loopsbench-runtime requires --only loopsbench or all")
    root = repository_root()
    assets = a.assets_root.expanduser().resolve()
    assets.mkdir(parents=True, exist_ok=True)
    required = {
        "beyondswe": [a.beyondswe_source],
        "scbench": [a.scbench_runner, a.scbench_source],
        "loopsbench": [a.loopsbench_source],
    }
    selected = list(required) if a.only == "all" else [a.only]
    for k in selected:
        if any(x is None for x in required[k]):
            p.error(f"missing upstream paths for {k}")
    if "beyondswe" in selected:
        prepare_beyondswe(root, a.beyondswe_source.resolve(), assets)
    if "scbench" in selected:
        prepare_scbench(
            root, a.scbench_runner.resolve(), a.scbench_source.resolve(), assets
        )
    if "loopsbench" in selected:
        prepare_loopsbench(root, a.loopsbench_source.resolve(), assets)
        from looparena.commands.submission_runtime import (
            prepare_dependencies,
            build_images,
        )

        if a.loopsbench_runtime in {"dependencies", "all"}:
            prepare_dependencies(root, assets)
        if a.loopsbench_runtime in {"images", "all"}:
            build_images(root, a.loopsbench_source.resolve(), assets, a.loopsbench_task)
    print(
        "Prepared frozen evaluator task trees. Prepare source images, offline evaluator dependencies and Type II workspaces separately; run preflight before inference."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
