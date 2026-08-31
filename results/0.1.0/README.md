# LoopArena v0.1.0 results

This directory contains the public, path-free result release used by the project
website. `outcomes.jsonl` has one canonical Type II or Type III outcome per
setting, method, case, and repeat. It intentionally excludes private artifact
paths, provider endpoints, raw trajectories, and attempt-local metadata.

Recompute the aggregate scores and the paired Type II/Type III Controller rank
correlation from the 1,134 canonical outcomes in the arXiv main-result panel:

```bash
looparena-results-summarize results/0.1.0 \
  --json-out /tmp/looparena-v0.1.0-summary.json
```

The command validates the exact canonical key set before reporting a score. It
fails on a missing, duplicate, extra, or non-valid record. Strict Success Rate,
source splits, and repeat-level successes are recomputed from the JSONL file.
Type II/III confidence intervals are the 2.5th and 97.5th percentiles from
10,000 registered bootstrap draws (NumPy `Generator(PCG64)`, seed `20260822`).
Within each source, the procedure resamples parent tasks with replacement and
then resamples the three repeats for each sampled task and method; task draws
are shared across methods and settings, and method-specific repeat draws are
shared across paired Type II and Type III tasks. The complete specification is
stored in `manifest.json`. Standardized no-cache costs remain frozen publication
metadata because they require complete provider-usage records.

`tools/build_public_results.py` is the maintainer-side exporter. It accepts a
validated canonical-outcome ledger, full table, and registered statistics file;
it strips private fields and regenerates this directory plus
`docs/data/results.json` deterministically.
