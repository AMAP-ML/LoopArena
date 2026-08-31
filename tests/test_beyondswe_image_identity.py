from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from looparena.benchmarks import type3_runtime
from looparena.commands import type2_run


@pytest.mark.parametrize("runtime", [type2_run, type3_runtime])
def test_local_image_identity_inspects_the_pinned_platform(
    monkeypatch: pytest.MonkeyPatch,
    runtime,
) -> None:
    observed: list[str] = []

    def fake_run(command, **_):
        observed.extend(command)
        return SimpleNamespace(
            returncode=0,
            stdout='[{"Id":"sha256:pinned","Os":"linux","Architecture":"amd64"}]',
        )

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    assert (
        runtime._local_image_id(
            docker_executable="docker",
            reference="example/beyondswe:task",
            platform="linux/amd64",
        )
        == "sha256:pinned"
    )
    assert observed == [
        "docker",
        "image",
        "inspect",
        "--platform",
        "linux/amd64",
        "example/beyondswe:task",
    ]


@pytest.mark.parametrize("runtime", [type2_run, type3_runtime])
def test_beyondswe_image_identity_accepts_the_pinned_docker_id(
    monkeypatch: pytest.MonkeyPatch,
    runtime,
) -> None:
    pinned = "sha256:" + "1" * 64
    monkeypatch.setattr(runtime, "_local_image_id", lambda **_: pinned)

    observed = runtime._ensure_harbor_image(
        {
            "runtime_identity": {"image_id": pinned},
            "adapter_config": {"docker_executable": "docker"},
        },
        Path("/unused"),
        {
            "image_reference": "example/beyondswe:task",
            "platform": "linux/amd64",
        },
    )

    assert observed == pinned


@pytest.mark.parametrize("runtime", [type2_run, type3_runtime])
def test_beyondswe_image_identity_rejects_a_different_local_image(
    monkeypatch: pytest.MonkeyPatch,
    runtime,
) -> None:
    pinned = "sha256:" + "1" * 64
    observed = "sha256:" + "2" * 64
    monkeypatch.setattr(runtime, "_local_image_id", lambda **_: observed)

    with pytest.raises(RuntimeError, match="image identity mismatch"):
        runtime._ensure_harbor_image(
            {
                "runtime_identity": {"image_id": pinned},
                "adapter_config": {"docker_executable": "docker"},
            },
            Path("/unused"),
            {
                "image_reference": "example/beyondswe:task",
                "platform": "linux/amd64",
            },
        )
