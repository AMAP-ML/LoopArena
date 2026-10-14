Build a Python graph-processing stack in `/workspace` on top of the existing
`PropertyGraph` implementation in `graph.py` and the sample graphs in `data/`.
The task is organized as three layers that should reuse one another rather
than acting like separate toy projects:

`graphx.py`
Add a GraphX-style analytics layer with a Pregel API and the core algorithms
the tests expect, including PageRank, connected components, and triangle
counting.

`graphframes.py`
Extend the same substrate with a GraphFrames-style API for graph operations
expressed through DataFrame-like transformations and pattern matching.

`graphscope.py`
Finish with a GraphScope-style unified engine that supports Gremlin traversal,
PIE-style iterative execution, and the required built-in algorithms such as
SSSP, LPA, and sampling.

Use `requirements/` as the source of truth for exact behavior and experiment
outputs. `run.sh` must run the required experiments and print `KEY: VALUE`
lines for the harness.
