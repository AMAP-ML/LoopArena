Build a Python stack that connects compiler IR infrastructure, hardware IR, and
formal circuit semantics. Work in `/workspace`, and treat the three layers as
one evolving system:

`src/mlir/`
Implement the core MLIR model: operations, blocks, regions, modules, the text
parser and printer, a pass manager, and the greedy rewrite/constant-folding
machinery needed by the arithmetic sample input.

`src/circt/`
Extend that foundation with CIRCT-style hardware dialect support for `hw`,
`comb`, and `seq`, plus the cycle-accurate circuit simulator used by the ALU
and counter inputs.

`src/k_circt/`
Add the K-inspired semantic layer: symbol and type analysis, stateful circuit
execution, and the Kimulator trace engine that evaluates circuits through
explicit semantic rules.

Keep the modules connected so later stages reuse the earlier IR and parsing
infrastructure. `python3 tests/test_outputs.py` is the main test entry point,
and `src/run_experiments.py` must continue to emit the required `KEY: VALUE`
lines for the harness.
