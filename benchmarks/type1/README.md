# LoopArena Type I

Type I evaluates whether a model can choose the best next control decision from
four complete alternatives. Each question contains the exact model-facing
messages and one correct option (`A` through `D`). Scoring is deterministic
exact match on the final `Answer: X` line.

## Dataset

`questions.jsonl` is the released Type I dataset. It contains 90
questions with three fields:

- `id`: stable question identifier;
- `input`: system and user messages sent to the model;
- `ideal`: correct option, `A` through `D`.

`manifest.json` records the public dataset shape and evaluation condition.

## Run

```bash
looparena-type1-run \
  --data benchmarks/type1/questions.jsonl \
  --model MODEL_ID \
  --output results/MODEL_ID.jsonl
```

The runner writes one record per question to the requested JSONL path and an
aggregate summary to the corresponding `.summary.json` path.

The runner sends the stored system and user messages unchanged, with one
request per question and no harness retries. Qwen-family calls use
`temperature=0`. GPT and Claude gateway routes omit temperature because those
routes do not accept it; they otherwise use the same messages and output
budget. GPT and Claude prompt caching is handled by the shared gateway client
and does not change benchmark inputs or scoring.

Invalid model answers count as incorrect. If any gateway request fails, the
summary marks the run incomplete and leaves formal `accuracy` unavailable.
