<p align="center">
  <img src="docs/assets/looparena-hero.png" width="100%" alt="A coding workstation surrounded by Worker, Reporter, Controller, evidence, verification, and evaluation stations connected as a runtime loop">
  <img src="docs/assets/looparena-wordmark.png" width="100%" alt="LoopArena — Models as runtime controllers for Loop Engineering">
</p>

<p align="center"><strong>DreamX Team</strong></p>

<p align="center">
  <a href="https://arxiv.org/abs/2608.28281"><strong>📄 arXiv Paper</strong></a> ·
  <a href="https://huggingface.co/papers/2608.28281"><strong>🤗 Hugging Face Paper</strong></a> ·
  <a href="https://amap-ml.github.io/LoopArena/"><strong>🌐 Project Website</strong></a>
</p>

<p align="center">
  <a href="docs/protocol.md"><strong>Protocol</strong></a> ·
  <a href="#benchmark"><strong>Benchmark</strong></a> ·
  <a href="#quick-start"><strong>Quick start</strong></a>
</p>

<p align="center">
  <a href="pyproject.toml"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white"></a>
  <a href="LICENSE"><img alt="Code license: Apache 2.0" src="https://img.shields.io/badge/Code%20License-Apache--2.0-D22128"></a>
  <img alt="Release 0.1.0" src="https://img.shields.io/badge/Release-0.1.0-16A085">
</p>

**Loop Engineering** organizes long-running development work around coding
agents. Instead of writing each prompt by hand, practitioners design loops that
monitor progress, assign work, run checks, and decide what the agent should do
next. This creates a new evaluation question: which models make good decisions
inside that loop?

**LoopArena** benchmarks how well one model can guide a separate coding agent
through a long-running task. The evaluated model is the **Controller**; the
separate, fixed coding agent is the **Worker**. As the task unfolds, the
Controller reviews progress, decides what the Worker should do or verify next,
and determines when to stop. Every Controller uses the same Worker and
execution setup, so the scores compare runtime loop control under common coding
conditions.

## 🔁 How LoopArena works

<p>
  <img src="docs/assets/looparena-harness.png" width="100%" alt="The LoopArena harness compares Controller-guided and no-control execution from the same restored task state using the same coding Worker and source-native evaluator">
</p>

The Worker edits the repository and uses the tools available in the task
environment. Whenever it finishes an assignment, a temporary, read-only
Reporter summarizes the current state. The harness formats the report as an
**Evidence Packet** for the Controller, which returns a **Loop Contract** with
the next Worker assignment, a verification request, or a decision to stop. Only
the Worker can change the repository. The Controller has no coding tools or
access to private evaluator information.

Type II and Type III also report shared **no-control** and **fixed-control**
reference policies. They provide context for the Controller results but are not
part of the Controller-model ranking. The full information boundary, budgets,
contract schema, and failure accounting are defined in the
[public protocol](docs/protocol.md).

<a id="benchmark"></a>

## 🧪 Benchmark

LoopArena provides three complementary settings. Execution scope and evaluation
cost increase from Type I to Type III:

| Setting | What it measures | Starting point | Size | Scoring |
| --- | --- | --- | ---: | --- |
| **Type I · Contract selection** | Choose the best next Loop Contract from four execution-validated candidates | Frozen Evidence Packet at one control point | 90 | Contract Accuracy |
| **Type II · Condensed coding task** | Guide the Worker through one selected slice of a full coding task | Standardized task-slice workspace | 27 | Strict Success Rate |
| **Type III · Full coding task** | Guide the Worker through the complete coding task | Original task state | 27 | Strict Success Rate |

Type III provides the full-task anchor. Type II retains closed-loop control on
a task slice at lower cost. Type I moves candidate execution into benchmark
construction, so a new Controller can be scored through low-cost
multiple-choice decisions backed by execution outcomes.

Type II and Type III are paired one-to-one across **11 SCBench** and
**16 BeyondSWE** tasks. For SCBench, Type III executes every native checkpoint
in order; for BeyondSWE, it executes the complete task. See the package guides
for the exact case interface and run commands:

- [Type I: Contract selection](benchmarks/type1/README.md)
- [Type II: condensed coding tasks](benchmarks/type2/README.md)
- [Type III: full coding tasks](benchmarks/type3/README.md)

## 📊 Results

The v0.1.0 main panel compares five Controller models. The strongest Type III
Strict Success Rate is **24.69%**, leaving substantial headroom on full-task
control. Across Controllers, Type II reduces estimated inference cost by
**64.4%** on average while producing a similar Core-based ordering to Type III
(Spearman's **ρ = 0.9747**). All Controller-model Type II and Type III runs use
Qwen3.7-Plus as the shared Worker and Reporter; no-control and fixed-control are
reported separately as reference policies. Qwen3.7-Plus is also evaluated in
the Controller role.

| Controller | Type I Contract Acc. ↑ | Type II SSR ↑ | Type III SSR ↑ |
| --- | ---: | ---: | ---: |
| Qwen3.7-Plus | 72.22 | <u>48.15</u> | <u>23.46</u> |
| DeepSeek-V4-Flash-0731 | <u>77.78</u> | 45.68 | 19.75 |
| GLM 5.2 | 74.44 | 37.04 | 16.05 |
| GPT-5.5 | **87.78** | **51.85** | **24.69** |
| Claude Opus 4.8 | 76.67 | <u>48.15</u> | 20.99 |

Fixed control scores 46.91% versus 39.51% for no control on Type II, but both
score 18.52% on Type III. This contrast motivates checking conclusions from
condensed tasks against complete-task behavior. Reference policies provide
context and are not part of the Controller ranking.

[Interactive leaderboard](https://amap-ml.github.io/LoopArena/#results) ·
[Released outcomes](results/0.1.0) · [Evaluation protocol](docs/protocol.md)

<a id="quick-start"></a>

## 🚀 Quick start

### 1. Install

LoopArena requires Python 3.10 or newer. For the Type I smoke test, install the
model-gateway dependency from the cloned repository root:

```bash
python -m pip install -e '.[gateway]'
```

Benchmark data live in the Git checkout rather than the Python wheel. For a
non-editable install, set `LOOPARENA_HOME` to the cloned repository root.

### 2. Configure a model endpoint

LoopArena uses an OpenAI-compatible model endpoint. Export the following
variables, or save them in a repository-local `.env` file using
[`.env.example`](.env.example) as a template:

```bash
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

The example uses Alibaba Cloud Model Studio's public OpenAI-compatible endpoint.
You may replace it with another compatible service. `OPENAI_BASE_URL` may be
omitted when using the default OpenAI endpoint.

### 3. Run one Type I question

Type I is the fastest way to evaluate a Controller. The smoke test below makes
one model call and does not run the Worker or use Docker:

```bash
looparena-type1-run \
  --data benchmarks/type1/questions.jsonl \
  --model MODEL_ID \
  --output results/type1-smoke.jsonl \
  --limit 1 \
  --concurrency 1
```

The command writes the model response to `results/type1-smoke.jsonl` and the
aggregate summary to `results/type1-smoke.summary.json`. Remove `--limit 1` to
evaluate the complete Type I set. The command returns a nonzero status if an
API call fails and the requested output is incomplete.

Add `--preflight-only` and omit `--output` to validate the data, model ID, and
endpoint configuration without making a model call.

### 4. Prepare upstream assets

Type II and Type III additionally require Git, Docker, and `uv` for SCBench.
Install the full runtime dependencies before preparing their evaluator assets:

```bash
python -m pip install -e '.[gateway,scbench]'
```

LoopArena includes the frozen Type II starting workspaces and small preparation
recipes, but does not redistribute complete upstream evaluator bundles,
repositories, or container images. Download the revisions pinned in
[`benchmarks/upstreams.toml`](benchmarks/upstreams.toml) from the original
projects:

- [BeyondSWE Harbor tasks](https://huggingface.co/datasets/AweAI-Team/BeyondSWE-harbor)
- [SCBench runner](https://github.com/SprocketLab/slop-code-bench)
- [SCBench problems](https://github.com/gabeorlanski/scb-problems)

Then arrange those local checkouts for LoopArena:

```bash
looparena-assets prepare \
  --assets-root ~/.cache/looparena/assets \
  --beyondswe-source /path/to/BeyondSWE-harbor \
  --scbench-runner /path/to/slop-code-bench \
  --scbench-source /path/to/scb-problems
export LOOPARENA_ASSETS_ROOT=~/.cache/looparena/assets
```

To prepare just one upstream family for an initial smoke test, add
`--only beyondswe` or `--only scbench` and omit the unrelated source flags.

The command does not access the network or build images. Pull the BeyondSWE
images and build the SCBench `linux/amd64` runtime according to the corresponding
upstream instructions.

### 5. Run a complete task

```bash
looparena-type3-run \
  --case-dir benchmarks/type3/cases/case203 \
  --assets-root "$LOOPARENA_ASSETS_ROOT" \
  --out-dir runs/case203 \
  --arm controlled \
  --seed 0 \
  --worker-model WORKER_MODEL_ID \
  --controller-model CONTROLLER_MODEL_ID
```

Add `--preflight-only` to validate the case, upstream assets, Docker runtime,
and evaluator identity before making a model call.

Use `--arm no-control` for the no-control reference. Provider credentials and
OpenAI-compatible endpoint settings are supplied through environment variables;
no credentials are stored in this repository.

Type II and Type III default both model roles to `qwen3.7-plus`. The
`--worker-model` and `--controller-model` flags can replace either role with the
model ID exposed by the selected provider.

For multi-case experiments, use `looparena-type2-panel` or
`looparena-type3-panel`. Panel runs are resumable and can be summarized with
the corresponding `looparena-type2-summarize` or
`looparena-type3-summarize` command. Every command provides `--help`.

Validated v0.1.0 aggregate results and path-free canonical Type II/III outcomes
are available under [`results/0.1.0`](results/0.1.0). Recompute the released
scores and paired Controller rank correlation with:

```bash
looparena-results-summarize results/0.1.0
```

## 🔬 Reproducibility

- Public case indexes define the released task cohorts.
- BeyondSWE runs verify the exact `linux/amd64` Docker image ID before any
  model call.
- [`benchmarks/upstreams.toml`](benchmarks/upstreams.toml) pins external source
  revisions; asset preparation is local and network-free.
- Model access ends before terminal evaluation.
- Valid evaluator failures remain model outcomes. Provider, runner, container,
  and evaluator infrastructure failures are reported separately.
- Formal runs record model configuration, runtime identity, token usage,
  trajectory evidence, and terminal evaluator receipts.

## 🗂️ Repository map

```text
benchmarks/          Type I, Type II, and Type III benchmark packages
src/looparena/       Harness, runtimes, evaluators, and command-line tools
tests/               Harness and release-package tests
docs/protocol.md     Public experimental protocol
docs/index.html      Dependency-free project website for GitHub Pages
results/0.1.0/       Public canonical outcomes and aggregate result release
```

LoopArena's own code and documentation are licensed under
[Apache-2.0](LICENSE). Third-party benchmark material and frozen source
snapshots retain their source terms; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
