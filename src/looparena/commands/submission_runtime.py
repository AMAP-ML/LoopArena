"""Prepare public LoopsBench runtime dependencies and source-built images."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

from looparena.commands.workspaces import safe
from looparena.evaluators.loopsbench import task_tree_sha256


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(record: dict, destination: Path) -> None:
    """Download only an explicitly pinned public artifact, verifying before install."""
    if destination.exists():
        if digest(destination) != record["sha256"]:
            raise ValueError(f"existing download differs: {destination.name}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".download-")
    try:
        with os.fdopen(fd, "wb") as out, urlopen(record["url"], timeout=120) as source:
            shutil.copyfileobj(source, out)
        if (
            Path(temporary).stat().st_size != record["bytes"]
            or digest(Path(temporary)) != record["sha256"]
        ):
            raise ValueError(f"public download hash mismatch: {destination.name}")
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_dependencies(root: Path, assets: Path) -> None:
    config = root / "benchmarks/adaptations/loopsbench"
    lock_path = config / "runtime-lock.json"
    lock = json.loads(lock_path.read_text())
    target = assets / "evaluator_runtime"
    target.mkdir(parents=True, exist_ok=True)
    for row in lock["wheels"]:
        fetch(row, safe(target, row["path"]))
    uv = lock["uv"]
    wheel = assets / "downloads" / uv["filename"]
    fetch(uv, wheel)
    bin_root = target / "bin"
    bin_root.mkdir(exist_ok=True)
    with zipfile.ZipFile(wheel) as z:
        names = [n for n in z.namelist() if n.endswith(".data/scripts/uv")]
        if len(names) != 1:
            raise ValueError("uv wheel has no unique executable")
        data = z.read(names[0])
    destination = bin_root / "uv"
    if destination.exists() and destination.read_bytes() != data:
        raise ValueError("refusing to replace an existing uv runtime")
    destination.write_bytes(data)
    destination.chmod(0o755)
    for source in (config / "runtime-bin").iterdir():
        destination = bin_root / source.name
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise ValueError(
                f"refusing to replace existing runtime file: {source.name}"
            )
        shutil.copyfile(source, destination)
        destination.chmod(0o755)
    receipt = {

        "origin": "pinned-public-downloads",
        "lock_sha256": digest(lock_path),
        "files": {
            p.relative_to(target).as_posix(): digest(p)
            for p in sorted(target.rglob("*"))
            if p.is_file() and p.name != "PREPARED.json"
        },
    }
    (target / "PREPARED.json").write_text(json.dumps(receipt, indent=2) + "\n")
    verify_dependencies(root, target)


def verify_dependencies(root: Path, target: Path) -> None:
    config = root / "benchmarks/adaptations/loopsbench"
    lock_path = config / "runtime-lock.json"
    lock = json.loads(lock_path.read_text())
    receipt_path = target / "PREPARED.json"
    actual = {
        p.relative_to(target).as_posix()
        for p in target.rglob("*")
        if p.is_file() and p != receipt_path
    }
    if not receipt_path.is_file():
        raise ValueError("runtime is not prepared; run looparena-prepare --loopsbench-runtime dependencies")
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt.get("origin") != "pinned-public-downloads"
        or receipt.get("lock_sha256") != digest(lock_path)
    ):
        raise ValueError("runtime preparation receipt differs from pinned lock")
    expected = {x["path"]: x["sha256"] for x in lock["wheels"]}
    for source in (config / "runtime-bin").iterdir():
        expected["bin/" + source.name] = digest(source)
    uv = lock["uv"]
    wheel = target.parent / "downloads" / uv["filename"]
    if (
        not wheel.is_file()
        or digest(wheel) != uv["sha256"]
        or wheel.stat().st_size != uv["bytes"]
    ):
        raise ValueError(
            "pinned uv wheel missing or changed; keep the verified download"
        )
    with zipfile.ZipFile(wheel) as z:
        names = [n for n in z.namelist() if n.endswith(".data/scripts/uv")]
        if len(names) != 1:
            raise ValueError("invalid uv executable wheel")
        expected["bin/uv"] = hashlib.sha256(z.read(names[0])).hexdigest()
    if actual != set(expected) or receipt.get("files") != expected:
        raise ValueError("prepared runtime has extra, missing, or unbound files")
    for name, fingerprint in expected.items():
        path = safe(target, name)
        if digest(path) != fingerprint:
            raise ValueError(f"prepared runtime content changed: {name}")
    for row in lock["wheels"]:
        if safe(target, row["path"]).stat().st_size != row["bytes"]:
            raise ValueError("prepared wheel size changed")


def image_reference(task: str) -> str:
    return f"looparena-source/{task}:runtime"


def build_images(
    root: Path, source: Path, assets: Path, selected: list[str] | None = None
) -> None:
    profiles = json.loads(
        (root / "benchmarks/type3/LOOPSBENCH_IMAGE_ASSETS.json").read_text()
    )["cases"]
    recipes = json.loads(
        (root / "benchmarks/adaptations/loopsbench/recipes.json").read_text()
    )["recipes"]
    by_task = {x["task"]: x for x in recipes}
    tasks = {x["task_id"]: x for x in profiles.values()}
    chosen = sorted(tasks) if not selected else sorted(set(selected))
    if not set(chosen) <= set(tasks):
        raise ValueError("unknown LoopsBench task")
    output = assets / "source-image-receipts"
    output.mkdir(parents=True, exist_ok=True)
    for task in chosen:
        tree = safe(source, "tasks/" + task)
        profile = tasks[task]
        if (
            task_tree_sha256(tree) != by_task[task]["upstream_tree_sha256"]
            or digest(tree / "Dockerfile") != profile["dockerfile_sha256"]
        ):
            raise ValueError(f"upstream task identity changed: {task}")
        dockerfile = (tree / "Dockerfile").read_text()
        # The frozen adapter supplies patch and suppresses curl-only apt setup.
        # Supply those ordinary runtime tools in rebuilt images, including sources
        # whose Dockerfile omits them, without placing evaluator data in the image.
        dockerfile += "\nUSER root\nRUN (command -v patch && command -v curl) || (apt-get update && apt-get install -y patch curl && rm -rf /var/lib/apt/lists/*)\n"
        if (
            hashlib.sha256(dockerfile.encode()).hexdigest()
            != profile["source_build_dockerfile_sha256"]
        ):
            raise ValueError("source build recipe differs from the release manifest")
        reference = image_reference(task)
        subprocess.run(
            [
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "-t",
                reference,
                "-f",
                "-",
                str(tree),
            ],
            input=dockerfile,
            text=True,
            check=True,
        )
        inspection = json.loads(
            subprocess.run(
                ["docker", "image", "inspect", reference],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )[0]
        if inspection["Os"] != "linux" or inspection["Architecture"] != "amd64":
            raise ValueError("wrong reconstructed image platform")
        receipt = {

            "origin": "upstream-source-build",
            "task_id": task,
            "platform": "linux/amd64",
            "image_id": inspection["Id"],
            "image_reference": reference,
            "upstream_tree_sha256": by_task[task]["upstream_tree_sha256"],
            "dockerfile_sha256": profile["dockerfile_sha256"],
            "build_dockerfile_sha256": hashlib.sha256(dockerfile.encode()).hexdigest(),
            "prepared_task_tree_sha256": profile["task_tree_sha256"],
            "reference_image_id": profile["image_id"],
        }
        (output / f"{task}.json").write_text(json.dumps(receipt, indent=2) + "\n")


def prepared_image(assets: Path, profile: dict, *, docker: str) -> dict | None:
    receipt_path = assets / "source-image-receipts" / f"{profile['task_id']}.json"
    if not receipt_path.is_file():
        return None
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt.get("origin") != "upstream-source-build"
        or receipt.get("platform") != "linux/amd64"
        or receipt.get("image_reference") != image_reference(profile["task_id"])
        or receipt.get("upstream_tree_sha256") != profile["upstream_tree_sha256"]
        or receipt.get("build_dockerfile_sha256")
        != profile["source_build_dockerfile_sha256"]
        or receipt.get("task_id") != profile["task_id"]
        or receipt.get("dockerfile_sha256") != profile["dockerfile_sha256"]
        or receipt.get("prepared_task_tree_sha256") != profile["task_tree_sha256"]
        or receipt.get("reference_image_id") != profile["image_id"]
    ):
        raise ValueError("source-built image receipt disagrees with frozen task")
    proc = subprocess.run(
        [docker, "image", "inspect", receipt["image_reference"]],
        capture_output=True,
        text=True,
        check=True,
    )
    image = json.loads(proc.stdout)[0]
    if (
        image["Id"] != receipt["image_id"]
        or image["Os"] != "linux"
        or image["Architecture"] != "amd64"
    ):
        raise ValueError("source-built image identity changed; rebuild explicitly")
    return receipt
