Build a verification stack around Z3's Python API. The workspace already
contains graph fixtures, transition-system examples, and Horn-clause
benchmarks; use those as the common workload while you implement five layers:

`smt_solver/`
General SMT-based solving and optimization tasks such as N-Queens, graph
coloring, and scheduling.

`fixedpoint/`
Fixedpoint / Datalog analyses over the provided graphs and Horn programs,
including reachability and reaching-definitions style reasoning.

`spacer_verifier/`
Safety verification with PDR / Spacer-style reasoning over transition systems
such as Bakery-style mutual exclusion.

`verification/horn_translator.py`
A translator from a small imperative language into Horn clauses, including the
parser, AST, encoding, and verification flow.

`verification/cex_minimizer.py`
A CEGAR-style counterexample minimization and refinement loop with validation,
interpolation, predicate refinement, and trace minimization.

Treat these as one evolving verification toolbox rather than isolated demos.
Use `requirements/` for the exact contracts and metrics. `run.sh` must execute
all required experiments and emit `KEY: VALUE` lines for the harness.
