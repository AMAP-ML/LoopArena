#!/usr/bin/env python3
"""Build sanitized public result assets from a validated paper-data release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from looparena.commands.results_summarize import summarize_release

METHOD_IDS = {
    "No control": "none",
    "Fixed control": "non-adaptive-fixed",
    "Qwen3.7-Plus": "qwen3.7-plus",
    "DeepSeek-V4-Flash-0731": "deepseek-v4-flash-0731",
    "GLM 5.2": "glm-5.2",
    "GPT-5.5": "gpt-5.5-0424-global",
    "Claude Opus 4.8": "claude-opus-4-8",
}
METHOD_ORDER = tuple(METHOD_IDS)


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def public_role(method: str) -> str:
    return "reference" if method in {"No control", "Fixed control"} else "controller"


def build(args: argparse.Namespace) -> None:
    canonical = read_json(args.canonical_outcomes)
    full = read_json(args.full_table)
    statistics = read_json(args.statistics)
    bootstrap = statistics["bootstrap"]
    if not isinstance(bootstrap, dict):
        raise TypeError(f"{args.statistics}: bootstrap must be a JSON object")
    records = []
    for source in canonical["records"]:
        setting = {"Type II": "type2", "Type III": "type3"}.get(source["setting"])
        method = source["method"]
        if setting not in {"type2", "type3"}:
            raise ValueError(f"unexpected canonical setting: {source['setting']}")
        if method not in METHOD_IDS:
            continue
        records.append(
            {
                "setting": setting,
                "method": method,
                "role": public_role(method),
                "source": source["source"],
                "case_id": source["case_id"],
                "repeat_id": source["repeat"],
                "strict_valid": source["strict_valid"],
                "policy_pass": {
                    policy: source["policy_pass"][policy]
                    for policy in ("core-cases", "all-cases", "all-non-error")
                },
            }
        )
    records.sort(
        key=lambda row: (
            row["setting"],
            METHOD_ORDER.index(row["method"]),
            row["case_id"],
            row["repeat_id"],
        )
    )

    methods = [
        {"method": method, "model_id": model_id, "role": public_role(method)}
        for method, model_id in METHOD_IDS.items()
    ]
    published_statistics = {}
    for setting in ("type2", "type3"):
        published_statistics[setting] = {
            method: {
                "ci95": full[setting][method]["ci95_percent"],
                "mean_cost_usd_per_run": round(
                    full[setting][method]["resources"]["mean_usd_per_run"], 6
                ),
            }
            for method in METHOD_ORDER
        }

    type1 = []
    for method in METHOD_ORDER:
        if public_role(method) != "controller":
            continue
        row = full["type1"][method]
        type1.append(
            {
                "method": method,
                "model_id": METHOD_IDS[method],
                "role": "controller",
                "correct": row["correct"],
                "questions": row["questions"],
                "score": row["accuracy_percent"],
                "invalid_rate": round(
                    100 * row["invalid_answers"] / row["questions"], 2
                ),
                "cost_usd": row["estimated_no_cache_cost_usd_90_questions"],
            }
        )

    resource_comparison = full["paired_type2_type3_resource_comparison"][
        "controller_methods"
    ]
    controller_cost_reductions = [
        resource_comparison[method]["type2_cost_reduction_percent"]
        for method in METHOD_ORDER
        if public_role(method) == "controller"
    ]

    manifest = {
        "schema_version": "looparena.public_results_manifest.v1",
        "release": args.release,
        "validated_on": args.validated_on,
        "headline_policy": "core-cases",
        "repeat_ids": [0, 1, 2],
        "methods": methods,
        "expected_cases": {
            setting: sorted(
                {row["case_id"] for row in records if row["setting"] == setting}
            )
            for setting in ("type2", "type3")
        },
        "type1": type1,
        "published_statistics": published_statistics,
        "uncertainty": {
            "interval": "95% percentile bootstrap interval",
            "algorithm": bootstrap["algorithm"],
            "draws": bootstrap["draws"],
            "repeat_count": bootstrap["repeat_count"],
            "rng": bootstrap["rng"],
            "seed": bootstrap["seed"],
            "sources": bootstrap["sources"],
        },
        "findings": {
            "mean_type2_cost_reduction_percent": round(
                sum(controller_cost_reductions) / len(controller_cost_reductions), 1
            )
        },
        "provenance": {
            "description": "Sanitized canonical outcomes from the validated LoopArena v0.1.0 result release.",
            "omitted_private_fields": [
                "artifact paths",
                "provider endpoints",
                "raw trajectories",
                "attempt-local metadata",
            ],
        },
    }

    args.results_root.mkdir(parents=True, exist_ok=True)
    write_json(args.results_root / "manifest.json", manifest)
    with (args.results_root / "outcomes.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )

    summary = summarize_release(args.results_root)
    write_json(args.results_root / "summary.json", summary)

    settings = {}
    for setting in ("type2", "type3"):
        data = summary["settings"][setting]
        data["uncertainty_label"] = "95% CI"
        data["note"] = (
            (
                "27 paired task slices × 3 repeats. "
                if setting == "type2"
                else "27 complete tasks × 3 repeats. "
            )
            + "SCBench uses the registered Core-check criterion; reference policies are excluded from Controller ranking. "
            + "Mean cost sums every model call made by the policy under the frozen no-cache price schedule. "
            + f"Intervals are 95% percentiles from {bootstrap['draws']:,} registered source-stratified task-and-repeat bootstrap draws ({bootstrap['rng']}, seed {bootstrap['seed']})."
        )
        settings[setting] = data
    settings["type1"] = {
        "label": "Type I",
        "metric": "Contract Accuracy",
        "uncertainty_label": "Invalid Rate",
        "cost_label": "Cost / 90 questions",
        "note": "One response per question and no Worker execution. Cost covers 90 Controller responses under the frozen no-cache price schedule and excludes the one-time candidate-execution cost paid during benchmark construction.",
        "rows": summary["type1"],
    }
    website = {
        "schema_version": "looparena.website_results.v1",
        "release": summary["release"],
        "validated_on": summary["validated_on"],
        "headline_policy": "Core checks",
        "controllers": len(
            [entry for entry in methods if entry["role"] == "controller"]
        ),
        "findings": summary["findings"],
        "uncertainty": manifest["uncertainty"],
        "settings": {key: settings[key] for key in ("type1", "type2", "type3")},
    }
    write_json(args.website_json, website)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-outcomes", type=Path, required=True)
    parser.add_argument("--full-table", type=Path, required=True)
    parser.add_argument("--statistics", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, default=Path("results/0.1.0"))
    parser.add_argument(
        "--website-json", type=Path, default=Path("docs/data/results.json")
    )
    parser.add_argument("--release", default="0.1.0")
    parser.add_argument("--validated-on", default="2026-08-28")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
