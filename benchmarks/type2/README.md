# LoopArena Type II

Type II contains 27 single-checkpoint coding tasks paired one-to-one with the
27 complete Type III tasks: 11 from SCBench and 16 from BeyondSWE.

## Semantics

- Each task starts from a frozen, correct predecessor workspace and asks the
  model to complete one selected public increment.
- SCBench uses one preregistered native checkpoint and its pinned upstream
  checkpoint evaluator.
- BeyondSWE uses one preregistered semantic increment of the pinned upstream
  task and its source-image runtime.
- Controlled and no-control runs start from independent restores of the same
  workspace, task, public messages, tools, budget, and evaluator.
- Formal runs use the declared `linux/amd64` images.

## Layout

- `CASE_INDEX.json`: canonical 27-task inventory and Type III pairing;
- `cases/`: task text, public start messages, workspace, evaluator plan, and
  source provenance for each task;
- `../upstreams.toml`: pinned public upstream sources.

Each case has the same six-file interface:

```text
case.json
task.txt
public_messages.json
workspace.tar.gz
evaluator_plan.json
provenance.json
```

`selection_stage` records how a checkpoint was sampled (`early`, `middle`,
`late`, or an unstratified rule). It is not a task-difficulty label; this
release does not claim difficulty annotations for the 27 tasks.

## Prepare assets

```bash
looparena-assets prepare \
  --assets-root "$LOOPARENA_ASSETS_ROOT" \
  --beyondswe-source /path/to/BeyondSWE-harbor \
  --scbench-runner /path/to/slop-code-bench \
  --scbench-source /path/to/scb-problems
```

Add `--only beyondswe` or `--only scbench` to prepare one upstream family.

The asset command prepares all 27 evaluators from the pinned upstream
downloads. For four BeyondSWE intermediate checkpoints, it selects the
corresponding upstream fail-to-pass tests; two of those recipes also apply the
small fixture repairs required to run the upstream tests. Test assertions,
pass-to-pass tests, runners, images, and scoring rules remain unchanged.

## Run one case

```bash
looparena-type2-run \
  --case-dir benchmarks/type2/cases/CASE_ID \
  --arm controlled \
  --seed 0 \
  --worker-model MODEL_ID \
  --controller-model MODEL_ID \
  --assets-root "$LOOPARENA_ASSETS_ROOT" \
  --out-dir /path/to/results
```

For resumable multi-case execution, pass a provider-neutral plan to
`looparena-type2-panel`. Start from `panel.example.json`:

```bash
looparena-type2-panel \
  --plan benchmarks/type2/panel.example.json \
  --out-dir runs/type2 \
  --assets-root "$LOOPARENA_ASSETS_ROOT"
looparena-type2-summarize runs/type2
```

Add `--preflight-only` to the panel command to validate all selected cases and
return immediately without starting a model call or run.

The example is fail-closed: if any selected case is not ready, no panel jobs
start. Set `execution.continue_on_preflight_failure` to `true` only when an
explicitly partial panel is intended; blocked cases remain visible in status
and are not scored as model outcomes.
