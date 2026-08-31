import json
from pathlib import Path

import pytest

from looparena.commands.results_summarize import summarize_release

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPOSITORY_ROOT / "results" / "0.1.0"
CONTROLLERS = {
    "Qwen3.7-Plus",
    "DeepSeek-V4-Flash-0731",
    "GLM 5.2",
    "GPT-5.5",
    "Claude Opus 4.8",
}


def test_public_result_release_recomputes_published_headlines() -> None:
    summary = summarize_release(RESULTS_ROOT)

    assert summary["records"] == 1134
    assert summary["canonical_key_set_complete"] is True
    assert summary["findings"] == {
        "best_type3_ssr_percent": 24.69,
        "mean_type2_cost_reduction_percent": 64.4,
        "type2_type3_spearman_rho": 0.974679,
    }
    deepseek = next(
        row
        for row in summary["settings"]["type2"]["rows"]
        if row["method"] == "DeepSeek-V4-Flash-0731"
    )
    assert deepseek["successes"] == 37
    assert deepseek["successes_by_repeat"] == {"0": 13, "1": 12, "2": 12}


def test_committed_summary_and_website_match_recomputation() -> None:
    recomputed = summarize_release(RESULTS_ROOT)
    committed = json.loads((RESULTS_ROOT / "summary.json").read_text(encoding="utf-8"))
    website = json.loads(
        (REPOSITORY_ROOT / "docs" / "data" / "results.json").read_text(encoding="utf-8")
    )

    assert committed == recomputed
    assert website["findings"] == recomputed["findings"]
    assert website["controllers"] == 5
    assert website["uncertainty"] == committed["uncertainty"]
    assert website["uncertainty"] == {
        "algorithm": "Within each source, resample parent tasks with replacement. For each sampled task and method, resample three repeat IDs with replacement; reuse that method-specific repeat draw across Type II and Type III. Reuse the task draw across all methods and settings.",
        "draws": 10000,
        "interval": "95% percentile bootstrap interval",
        "repeat_count": 3,
        "rng": "numpy.random.Generator(PCG64)",
        "seed": 20260822,
        "sources": {"BeyondSWE": 16, "SCBench": 11},
    }
    assert website["settings"]["type1"]["uncertainty_label"] == "Invalid Rate"
    assert all(
        row["invalid_rate"] == 0.0 for row in website["settings"]["type1"]["rows"]
    )
    for setting in ("type2", "type3"):
        expected = {
            row["method"]: row["score"]
            for row in recomputed["settings"][setting]["rows"]
        }
        actual = {
            row["method"]: row["score"] for row in website["settings"][setting]["rows"]
        }
        assert actual == expected
        assert {
            row["method"]
            for row in website["settings"][setting]["rows"]
            if row["role"] == "controller"
        } == CONTROLLERS

    html = (REPOSITORY_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for row in recomputed["settings"]["type3"]["rows"]:
        if row["role"] == "controller":
            assert f"{row['score']:.2f}%" in html


def test_public_result_release_rejects_missing_record(tmp_path: Path) -> None:
    manifest = (RESULTS_ROOT / "manifest.json").read_text(encoding="utf-8")
    outcomes = (
        (RESULTS_ROOT / "outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    )
    (tmp_path / "manifest.json").write_text(manifest, encoding="utf-8")
    (tmp_path / "outcomes.jsonl").write_text(
        "\n".join(outcomes[1:]) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="canonical key set differs"):
        summarize_release(tmp_path)
