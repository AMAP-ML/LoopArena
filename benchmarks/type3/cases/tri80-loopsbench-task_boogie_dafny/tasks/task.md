Build a Z3-based verification stack in Python under `/workspace`. This task has
three layers that genuinely depend on each other: a Boogie verifier, a
Dafny-to-Boogie verification pipeline, and an IronFleet-style distributed lock
service proof.

Implement them in this order:

`boogie_verifier.py`
Build a usable BoogiePL verifier with the full typed AST, parser, weakest-
precondition and VC generation pipeline, SSA-aware procedure verification,
loop handling with invariants, SMT/Z3 encoding, counterexamples, and
verification diagnostics. It must verify the programs in
`test_programs/boogie/`.

`dafny_translator.py`
Build a Dafny front end and verifier that can parse the richer Dafny language,
check types and termination, handle refinement and induction machinery, lower
Dafny constructs into Boogie, and then verify the result with Z3 and
path-sensitive VC generation. It must verify the programs in
`test_programs/dafny/`.

`ironfleet_lock.py`
Model and verify a distributed lock service using layered refinement: protocol
and specification state machines, safety invariants, abstraction mapping,
forward simulation, bounded liveness/fairness reasoning, and a multi-host
network model checked with Z3 bounded model checking for three nodes.

Keep the implementation focused on end-to-end verification rather than on
isolated parsers. The deliverable is a working stack that passes
`pytest /tests/{boogie,dafny,ironfleet}/` and whose per-module `run.sh`
scripts emit the required `KEY: VALUE` experiment lines. Use the files in
`requirements/` as the source of truth for exact contracts and metrics.
