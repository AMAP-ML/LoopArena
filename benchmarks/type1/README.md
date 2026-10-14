# Type I

160 single-choice questions, each containing four complete control contracts.
The JSONL files in `questions/` contain `id`, model-facing `input` messages,
and the reference choice `ideal` (A–D).
An option that stops the agent needs only the terminal decision; continuing
options include a Worker assignment.

```sh
looparena-type1-run --data benchmarks/type1/questions \
  --model MODEL_ID --output runs/type1.jsonl
```

Each question makes one model request with the stored messages, a requested
seed of 0, temperature 0 and a 20,480-token output budget. Provider routes that
do not support seed or temperature omit those parameters. No question is
retried automatically. The requested answer format is `Answer: X`.

The scorer compares the parsed A–D choice with the reference answer. Unparseable
answers count as incorrect. Request failures leave the panel incomplete and
its formal accuracy unavailable. Predictions and a summary are written to the
requested output location.
