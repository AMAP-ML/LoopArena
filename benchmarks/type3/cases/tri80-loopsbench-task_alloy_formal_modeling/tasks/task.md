Build a Python implementation of the Alloy toolchain family in the workspace.
The repository already includes the base relational data structures
(`Atom`, `TupleS`, `Universe`, `Relation`, `Bounds`) and some relational-logic
AST scaffolding. Your job is to finish the four layers that sit on top of that
foundation and make them work together.

Implement the stack in dependency order:

`alloy/`
Build the core Alloy front end: the full relational-expression and formula AST,
a Lark-based parser for signatures, fields, facts, predicates, functions, and
assertions, type and scope resolution, multiplicity handling, and translation
from model structure into relational constraints.

`kodkod/`
Add the SAT-backed relational model finder: boolean-matrix encoding for
relations, CNF generation for the supported relational constraints, symmetry
breaking, Minisat22-based solving, and model enumeration through blocking
clauses.

`pardinus/`
Extend the system with temporal model finding: temporal AST support, bounded
model checking by unrolling mutable relations over time, decomposed solving for
static versus temporal partitions, and trace extraction with lasso detection.

`electrum/`
Finish the temporal Alloy layer: parser extensions for mutable relations and
priming, infinite-trace/lasso semantics, analyzer modes for consistency,
safety, and liveness, temporal fact classification, and counterexample
animation with step-by-step change highlighting.

Keep the dependency chain real: later layers should build on the earlier ones
rather than reimplementing them in isolation. Use `requirements/` as the
source of truth for the exact behavior, experiments, and expected outputs.
`run.sh` must execute all required experiments and print `KEY: VALUE` lines for
the harness.
