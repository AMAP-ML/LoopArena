from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


POLICIES = ("core-cases", "all-cases", "all-non-error")


def _read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number}: expected a JSON object")
                records.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    return records


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and values[order[end]] == values[order[cursor]]:
            end += 1
        average = (cursor + 1 + end) / 2
        for position in range(cursor, end):
            ranks[order[position]] = average
        cursor = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_scale = math.sqrt(sum((x - left_mean) ** 2 for x in left))
    right_scale = math.sqrt(sum((y - right_mean) ** 2 for y in right))
    if left_scale == 0 or right_scale == 0:
        raise ValueError("Controller scores do not define a rank correlation")
    return numerator / (left_scale * right_scale)


def _validate_record(record: dict[str, Any], line_number: int) -> None:
    required = {
        "setting",
        "method",
        "role",
        "source",
        "case_id",
        "repeat_id",
        "strict_valid",
        "policy_pass",
    }
    if set(record) != required:
        missing = sorted(required - set(record))
        extra = sorted(set(record) - required)
        raise ValueError(
            f"outcomes line {line_number}: fields differ; missing={missing}, extra={extra}"
        )
    if record["setting"] not in {"type2", "type3"}:
        raise ValueError(
            f"outcomes line {line_number}: unknown setting {record['setting']!r}"
        )
    if record["role"] not in {"controller", "reference"}:
        raise ValueError(
            f"outcomes line {line_number}: unknown role {record['role']!r}"
        )
    if record["source"] not in {"SCBench", "BeyondSWE"}:
        raise ValueError(
            f"outcomes line {line_number}: unknown source {record['source']!r}"
        )
    if not isinstance(record["repeat_id"], int) or isinstance(
        record["repeat_id"], bool
    ):
        raise ValueError(f"outcomes line {line_number}: repeat_id must be an integer")
    if not isinstance(record["strict_valid"], bool):
        raise ValueError(f"outcomes line {line_number}: strict_valid must be Boolean")
    if set(record["policy_pass"]) != set(POLICIES):
        raise ValueError(
            f"outcomes line {line_number}: policy_pass must contain {POLICIES}"
        )
    if not all(isinstance(value, bool) for value in record["policy_pass"].values()):
        raise ValueError(f"outcomes line {line_number}: policy results must be Boolean")


def summarize_release(results_root: Path) -> dict[str, Any]:
    manifest = _read_json(results_root / "manifest.json")
    records = _read_jsonl(results_root / "outcomes.jsonl")
    if manifest.get("schema_version") != "looparena.public_results_manifest.v1":
        raise ValueError("unsupported results manifest schema")
    for line_number, record in enumerate(records, start=1):
        _validate_record(record, line_number)

    methods = manifest["methods"]
    method_by_name = {entry["method"]: entry for entry in methods}
    expected_keys: set[tuple[str, str, str, int]] = set()
    for setting in ("type2", "type3"):
        for method in method_by_name:
            for case_id in manifest["expected_cases"][setting]:
                for repeat_id in manifest["repeat_ids"]:
                    expected_keys.add((setting, method, case_id, repeat_id))

    seen: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for record in records:
        key = (
            record["setting"],
            record["method"],
            record["case_id"],
            record["repeat_id"],
        )
        if record["method"] not in method_by_name:
            raise ValueError(f"unknown method in outcomes: {record['method']}")
        if record["role"] != method_by_name[record["method"]]["role"]:
            raise ValueError(f"role mismatch for {record['method']}")
        if key in seen:
            raise ValueError(f"duplicate canonical outcome: {key}")
        seen[key] = record

    missing = sorted(expected_keys - set(seen))
    extra = sorted(set(seen) - expected_keys)
    if missing or extra:
        raise ValueError(
            f"canonical key set differs from manifest; missing={len(missing)}, extra={len(extra)}"
        )
    invalid = [key for key, record in seen.items() if not record["strict_valid"]]
    if invalid:
        raise ValueError(
            f"release contains {len(invalid)} non-valid canonical outcomes"
        )

    published = manifest["published_statistics"]
    settings: dict[str, Any] = {}
    for setting in ("type2", "type3"):
        setting_rows = []
        for method_meta in methods:
            method = method_meta["method"]
            subset = [
                record
                for key, record in seen.items()
                if key[0] == setting and key[1] == method
            ]
            policy = manifest["headline_policy"]
            successes = sum(record["policy_pass"][policy] for record in subset)
            by_repeat = {
                str(repeat_id): sum(
                    record["policy_pass"][policy]
                    for record in subset
                    if record["repeat_id"] == repeat_id
                )
                for repeat_id in manifest["repeat_ids"]
            }
            source_split = {
                source: {
                    "successes": sum(
                        record["policy_pass"][policy]
                        for record in subset
                        if record["source"] == source
                    ),
                    "runs": sum(record["source"] == source for record in subset),
                }
                for source in ("SCBench", "BeyondSWE")
            }
            publication = published[setting][method]
            setting_rows.append(
                {
                    **method_meta,
                    "successes": successes,
                    "runs": len(subset),
                    "score": round(100 * successes / len(subset), 2),
                    "ci95": publication["ci95"],
                    "cost_usd": publication["mean_cost_usd_per_run"],
                    "successes_by_repeat": by_repeat,
                    "source_split": source_split,
                }
            )
        settings[setting] = {
            "label": "Type II" if setting == "type2" else "Type III",
            "metric": "Strict Success Rate",
            "cost_label": "Mean cost / run",
            "rows": setting_rows,
        }

    controllers = [
        entry["method"] for entry in methods if entry["role"] == "controller"
    ]
    type2_scores = [
        next(
            row["score"] for row in settings["type2"]["rows"] if row["method"] == method
        )
        for method in controllers
    ]
    type3_scores = [
        next(
            row["score"] for row in settings["type3"]["rows"] if row["method"] == method
        )
        for method in controllers
    ]
    rho = _pearson(_average_ranks(type2_scores), _average_ranks(type3_scores))

    best_type3 = max(
        row["score"] for row in settings["type3"]["rows"] if row["role"] == "controller"
    )
    return {
        "schema_version": "looparena.public_results_summary.v1",
        "release": manifest["release"],
        "validated_on": manifest["validated_on"],
        "headline_policy": manifest["headline_policy"],
        "records": len(records),
        "canonical_key_set_complete": True,
        "type1": manifest["type1"],
        "uncertainty": manifest["uncertainty"],
        "settings": settings,
        "findings": {
            "best_type3_ssr_percent": best_type3,
            "mean_type2_cost_reduction_percent": manifest["findings"][
                "mean_type2_cost_reduction_percent"
            ],
            "type2_type3_spearman_rho": round(rho, 6),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recompute LoopArena v0.1.0 aggregate scores from public canonical outcomes."
    )
    parser.add_argument(
        "results_root",
        nargs="?",
        type=Path,
        default=Path("results/0.1.0"),
        help="release directory containing manifest.json and outcomes.jsonl",
    )
    parser.add_argument(
        "--json-out", type=Path, help="optional path for the computed summary"
    )
    args = parser.parse_args()
    try:
        summary = summarize_release(args.results_root.resolve())
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
