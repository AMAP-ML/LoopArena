Build a points-to analysis stack on top of Soufflé and Python. The workspace
already contains pre-extracted Java bytecode facts and type declarations; your
job is to turn those inputs into a sequence of increasingly precise analyses.

Implement the layers in order:

`analysis_ci.dl`
A context-insensitive Soufflé analysis with the core relations such as
`VarPointsTo`, `CallGraphEdge`, and `Reachable`.

`optimized_ci.dl`
An optimized Soufflé version of the same analysis, including the planning and
folding improvements needed to keep the Datalog execution efficient.

`analysis_cs.dl`
A one-call-site-sensitive Soufflé analysis that introduces context records and
the expected context-sensitive call-graph and points-to behavior.

`analysis/kcfa.py`
A Python k-CFA engine that generalizes call-site sensitivity with configurable
depth, explicit fixed-point iteration, context-sensitive virtual dispatch, and
context merging for scalability.

`analysis/field_sensitive.py`
A Python field-sensitive analysis with precise instance/static field tracking,
array abstraction, and type-aware heap refinement.

Treat the Python stages as explicit implementations of the same analysis ideas
that the earlier Soufflé stages introduced, not as unrelated side tasks. Use
`requirements/` for the exact contracts and metrics. `run.sh` must execute the
implemented analyses and report the required `KEY: VALUE` lines. The core
relation names, including `VarPointsTo`, `CallGraphEdge`, `Reachable`, and
`InstanceFieldPointsTo`, are part of the task contract and should remain
meaningful throughout the stack.
