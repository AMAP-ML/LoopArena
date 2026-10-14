"""Reconstruct frozen Type II inputs from user-provided upstream trees and small overlays."""

from __future__ import annotations
import argparse, gzip, hashlib, io, json, os, re, tarfile, tempfile
from pathlib import Path, PurePosixPath
from looparena.paths import repository_root


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def safe(root, relative):
    rel = PurePosixPath(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts or ".git" in rel.parts:
        raise ValueError(f"unsafe resource path: {relative}")
    p = (root / Path(*rel.parts)).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError(f"resource leaves source root: {relative}")
    return p


def added_file(patch: bytes, target: str) -> bytes:
    """Read only a new-file section of an upstream predecessor patch; execute no script."""
    for section in re.split(rb"(?=^diff --git )", patch, flags=re.M):
        match = re.search(rb"^\+\+\+ b/(.*)$", section, re.M)
        if not match or match.group(1).decode() != target:
            continue
        if b"--- /dev/null\n" not in section:
            raise ValueError("patch entry is not an added file")
        data = b""
        active = False
        for line in section.splitlines(keepends=True):
            if line.startswith(b"@@ "):
                active = True
                continue
            if not active:
                continue
            if line.startswith(b"+"):
                data += line[1:]
            elif line.startswith(b"\\ No newline"):
                data = data[:-1]
            elif line.strip():
                raise ValueError("unexpected added-file patch line")
        return data
    raise ValueError(f"upstream patch has no added file: {target}")


def recipe(case):
    desc = read(case / "workspace_asset.json")
    raw = (case / "workspace_recipe.json").read_bytes()
    if sha(raw) != desc["recipe_sha256"]:
        raise ValueError("workspace recipe hash mismatch")
    r = json.loads(raw)
    identity = [{k: v for k, v in x.items() if k != "source"} for x in r["files"]]
    digest = sha(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())
    if digest != r["workspace_tree_sha256"] or digest != desc["workspace_tree_sha256"]:
        raise ValueError("workspace tree binding mismatch")
    names = [x["path"] for x in r["files"]]
    if len(names) != len(set(names)):
        raise ValueError("duplicate workspace path")
    for x in r["files"]:
        safe(case, x["path"])
        if x["kind"] == "symlink":
            link = PurePosixPath(x["target"])
            if link.is_absolute():
                raise ValueError("absolute workspace link")
            dest = (case / x["path"]).parent / link
            if not dest.resolve().is_relative_to(case.resolve()):
                raise ValueError("workspace link escapes root")
    return desc, r


def verify_archive(case, archive):
    _, r = recipe(case)
    expected = {x["path"]: x for x in r["files"]}
    seen = set()
    with tarfile.open(archive, "r:gz") as bundle:
        for f in bundle:
            if f.name not in expected or f.name in seen:
                raise ValueError("unexpected or duplicate workspace member")
            seen.add(f.name)
            x = expected[f.name]
            if f.mode != x["mode"]:
                raise ValueError(f"workspace mode mismatch: {f.name}")
            if x["kind"] == "directory":
                if not f.isdir():
                    raise ValueError("workspace directory mismatch")
            elif x["kind"] == "symlink":
                if not f.issym() or f.linkname != x["target"]:
                    raise ValueError("workspace link mismatch")
            elif (
                not f.isfile()
                or f.size != x["bytes"]
                or sha(bundle.extractfile(f).read()) != x["sha256"]
            ):
                raise ValueError(f"workspace content mismatch: {f.name}")
    if seen != set(expected):
        raise ValueError("incomplete workspace")
    return r["workspace_tree_sha256"]


def materialize(case, upstream_root, assets_root):
    desc, r = recipe(case)
    destination = safe(assets_root, desc["relative_path_under_assets_root"])
    if destination.exists():
        verify_archive(case, destination)
        return destination
    overlay = case / "workspace_overlay.tar.gz"
    if sha(overlay.read_bytes()) != r["overlay_sha256"]:
        raise ValueError("workspace overlay hash mismatch")
    blobs = {}
    with tarfile.open(overlay, "r:gz") as t:
        for f in t:
            if (
                not f.isfile()
                or not re.fullmatch("[0-9a-f]{64}", f.name)
                or f.name in blobs
            ):
                raise ValueError("invalid overlay member")
            data = t.extractfile(f).read()
            if sha(data) != f.name:
                raise ValueError("overlay content mismatch")
            blobs[f.name] = data
    source_key = r["upstream"]["source_root_key"]
    source = safe(upstream_root, source_key) if source_key else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    patch_cache = {}
    fd, tmp = tempfile.mkstemp(prefix=".workspace-", dir=destination.parent)
    os.close(fd)
    tmp = Path(tmp)
    try:
        with (
            tmp.open("wb") as output,
            gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as gz,
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
        ):
            for x in r["files"]:
                member = tarfile.TarInfo(x["path"])
                member.mode = x["mode"]
                member.mtime = 0
                if x["kind"] == "directory":
                    member.type = tarfile.DIRTYPE
                    tar.addfile(member)
                    continue
                if x["kind"] == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = x["target"]
                    tar.addfile(member)
                    continue
                spec = x["source"]
                if spec["kind"] == "overlay":
                    data = blobs[spec["blob"]]
                elif spec["kind"] == "upstream":
                    data = safe(source, spec["path"]).read_bytes()
                elif spec["kind"] == "patch-added-file":
                    patch_path = safe(source, spec["patch"])
                    if spec["patch"] not in patch_cache:
                        raw = patch_path.read_bytes()
                        if sha(raw) != spec["patch_sha256"]:
                            raise ValueError("upstream predecessor patch changed")
                        patch_cache[spec["patch"]] = raw
                    data = added_file(patch_cache[spec["patch"]], spec["path"])
                else:
                    raise ValueError("unknown reconstruction source")
                if len(data) != x["bytes"] or sha(data) != x["sha256"]:
                    raise ValueError(
                        f"upstream content does not match frozen input: {x['path']}"
                    )
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        verify_archive(case, tmp)
        # Atomic create, refusing to overwrite a concurrently produced artifact.
        try:
            os.link(tmp, destination)
        except FileExistsError:
            verify_archive(case, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return destination


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case-dir", type=Path)
    p.add_argument("--all", action="store_true")
    p.add_argument("--upstream-root", type=Path, required=True)
    p.add_argument("--assets-root", type=Path, required=True)
    a = p.parse_args(argv)
    if bool(a.case_dir) == a.all:
        p.error("choose --case-dir or --all")
    cases = (
        sorted((repository_root() / "benchmarks/type2/cases").iterdir())
        if a.all
        else [a.case_dir]
    )
    for case in cases:
        print(
            materialize(
                case.resolve(), a.upstream_root.resolve(), a.assets_root.resolve()
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
