# LoopArena Type III

Type III contains 27 complete coding tasks from pinned official benchmarks:
11 from SCBench and 16 from BeyondSWE. Each row in `CASE_INDEX.json` is one
scientific task and one runnable case. Four legacy case IDs are compatibility
aliases only; they are not runnable entries.

## Semantics

- SCBench starts from its official empty workspace plus declared public static
  assets and executes every official checkpoint in order. The workspace
  persists across checkpoints while model and Controller context reset at each
  native boundary.
- BeyondSWE starts from the pinned source-image repository at the official task
  parent commit and executes the complete task.
- Formal evaluation uses only the evaluator shipped by the pinned upstream
  benchmark revision.

## Layout

- `CASE_INDEX.json`: canonical 27-task inventory and legacy aliases;
- `cases/`: task identities, ordered task sequences, task text, and evaluator
  plans;
- `BEYONDSWE_OFFICIAL_EVALUATORS.json`: source-native evaluator bindings;
- `../upstreams.toml`: pinned public upstream sources.

No case contains a saved workspace or serialized model conversation.

## Validate

```bash
python -m pip install -e '.[test]'
python -m pytest tests/test_type3_official_start.py
```

## Prepare assets

```bash
looparena-assets prepare \
  --assets-root "$LOOPARENA_ASSETS_ROOT" \
  --beyondswe-source /path/to/BeyondSWE-harbor \
  --scbench-runner /path/to/slop-code-bench \
  --scbench-source /path/to/scb-problems
```

Add `--only beyondswe` or `--only scbench` to prepare one upstream family.

The command only arranges files already downloaded from the official upstreams;
it does not download evaluator data or distribute container images.

## Run one case

```bash
looparena-type3-run \
  --case-dir benchmarks/type3/cases/CASE_ID \
  --arm controlled \
  --seed 0 \
  --worker-model MODEL_ID \
  --controller-model MODEL_ID \
  --assets-root "$LOOPARENA_ASSETS_ROOT" \
  --out-dir /path/to/results
```

Add `--preflight-only` to validate the case, upstream assets, Docker runtime,
and evaluator identity without starting a model call or run.

For a resumable multi-case run, pass a provider-neutral plan to
`looparena-type3-panel`; summarize it with `looparena-type3-summarize`:

```bash
looparena-type3-panel \
  --plan benchmarks/type3/panel.example.json \
  --out-dir runs/type3
looparena-type3-summarize --panel-dir runs/type3
```
