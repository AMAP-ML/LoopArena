# Data preparation and execution

Run from the source checkout after installing the package. Set `OPENAI_BASE_URL`
and `OPENAI_API_KEY` for an OpenAI-compatible service; see `.env.example`.
Model IDs must be supplied explicitly and must be accepted by the configured
endpoint. Actual runs require `--model` for Type I and `--worker-model` for
Type II/III; `--controller-model` is required only for a model Controller.
Single-case Type II/III resource preflight and origin preparation do not require
model IDs. Panel plans still require model IDs, including for preflight.

Only the selected credential variable is read. It defaults to `OPENAI_API_KEY`;
use `--api-key-env NAME` for another variable and `--controller-api-key-env NAME`
for a separate Controller credential. A separate Controller endpoint is selected
with `--controller-base-url`. There is no automatic fallback to other keys.

Keep credentials outside Git. Type I requires no task containers. Type II/III
require Linux Docker and the declared linux/amd64 runtime resources.

## Obtain upstream resources

Download the revisions in [upstreams.toml](../benchmarks/upstreams.toml):

| Material | Revision | Local directory |
| --- | --- | --- |
| [BeyondSWE-harbor](https://huggingface.co/datasets/AweAI-Team/BeyondSWE-harbor) | `7d2ced21b4c85f646d5ba8786875d7fbbe08ed49` | `upstream/BeyondSWE-harbor/` |
| [SCBench runner](https://github.com/SprocketLab/slop-code-bench) | `13de1a7a6b8b3dc5cc532a0c322a0997afa5bec7` | `upstream/slop-code-bench/` |
| [SCBench problems](https://github.com/gabeorlanski/scb-problems) | `4d38d300059667d57e43c31969bc455f5c338b52` | `upstream/scb-problems/` |
| [LoopsBench](https://huggingface.co/datasets/LoopsBench/LoopsBench) | `4104e5d5163617f5625e5bf0c23e607dd2019ccb` | `upstream/loopsbench/` |

For Git sources, clone and check out the revision. For Hugging Face, use
`hf download REPO --repo-type dataset --revision REVISION --local-dir DIRECTORY`.
Extract `loopsbench-tasks-84ae415.tar.zst` under `upstream/loopsbench/` to obtain
`tasks/`. Its SHA-256 is
`96774b4f1cb3c96107220384024bf97247f05123408f33f7c1c02c7e479dc619`.
The BeyondSWE directory must contain `beyondswe/`.

## Prepare tasks

```sh
export LOOPARENA_ASSETS_ROOT="$PWD/assets"
looparena-prepare --assets-root "$LOOPARENA_ASSETS_ROOT" \
  --beyondswe-source upstream/BeyondSWE-harbor \
  --scbench-runner upstream/slop-code-bench \
  --scbench-source upstream/scb-problems \
  --loopsbench-source upstream/loopsbench
```

This command uses local source files and the supplied adaptations. It verifies
content identities before preparing evaluator resources. `--only scbench`,
`--only beyondswe` and `--only loopsbench` select one source family.

Type II also needs its starting workspace. For each BeyondSWE case, read
`workspace_recipe.json` and clone `upstream.repository` at `upstream.revision`
into `upstream/SOURCE_ROOT_KEY/`, where SOURCE_ROOT_KEY is
`upstream.source_root_key`. Initialize pinned submodules with
`git submodule update --init --recursive`. LoopsBench uses the downloaded task
tree; SCBench starting code is supplied as small overlays. Then run:

```sh
looparena-workspaces --all --upstream-root upstream \
  --assets-root "$LOOPARENA_ASSETS_ROOT"
```

The command verifies workspace file contents, modes and links, and writes
`assets/type2/workspaces/CASE/workspace.tar.gz`.

## Prepare runtimes

Obtain BeyondSWE images according to the pinned upstream task instructions.
Build SCBench's image using the pinned runner's `slop-code docker build-base
configs/environments/docker-python3.12-uv.yaml` command. Before inference,
LoopArena checks `linux/amd64` and the upstream builder's
`io.slop-code.base-image-hash` label against the recipe in `upstreams.toml`.
A missing or different label requires rebuilding with `--force-build`.
The resolved local image ID is then used for the run and evaluator checks;
source builds on different machines need not have identical image IDs.

For LoopsBench:

```sh
looparena-prepare --only loopsbench --loopsbench-source upstream/loopsbench \
  --assets-root "$LOOPARENA_ASSETS_ROOT" --loopsbench-runtime all
```

This downloads the pinned public dependencies and builds 30 linux/amd64 images.
Use `--loopsbench-runtime dependencies` or `images` for one stage, or repeat
`--loopsbench-task task_NAME` to select tasks. The source tree, Dockerfile and
[runtime dependencies](../benchmarks/adaptations/loopsbench/runtime-lock.json)
are checked. Keep `assets/downloads/` with the prepared runtime.
Source-built images are identified by their build recipe and actual image ID;
upstream base-image and OS-package changes can affect reproducibility.

## Run and score

SCBench uses Core scoring: all Core checks must pass. The official evaluator
still executes the complete test suite; the other check groups do not determine
task success. Alternative SCBench scoring modes are not exposed.

```sh
looparena-type1-run --data benchmarks/type1/questions \
  --model MODEL_ID --output runs/type1.jsonl

looparena-type2-run --case-dir benchmarks/type2/cases/tri80-scbench-file_backup-cp4 \
  --assets-root "$LOOPARENA_ASSETS_ROOT" --out-dir runs/type2-example \
  --arm controlled --seed 0 --worker-model WORKER_MODEL_ID \
  --controller-model CONTROLLER_MODEL_ID --preflight-only

looparena-type3-run --case-dir benchmarks/type3/cases/tri80-scbench-file_backup \
  --assets-root "$LOOPARENA_ASSETS_ROOT" --out-dir runs/type3-example \
  --arm controlled --seed 0 --worker-model WORKER_MODEL_ID \
  --controller-model CONTROLLER_MODEL_ID --preflight-only
```

`--preflight-only` checks resources without model inference. Remove it to run.
Use `--arm no-control` for no control, or `--arm controlled
--controller-provider non-adaptive-fixed` for fixed control.

For panels, configure the supplied `panel.example.json` files. Set `seeds` to
`[0, 1, 2]` for three repeats; examples use one repeat for a small run.

```sh
looparena-type2-panel --plan benchmarks/type2/panel.example.json --out-dir runs/type2-panel --preflight-only
looparena-type3-panel --plan benchmarks/type3/panel.example.json --out-dir runs/type3-panel --preflight-only
looparena-type2-summarize runs/type2-panel --json-out runs/type2-summary.json
looparena-type3-summarize --panel-dir runs/type3-panel --out runs/type3-summary.json
```

Summaries retain task outcomes, budget/protocol validity and usage. API or
infrastructure failures are reported separately from scored task failures.
All commands provide `--help`.
