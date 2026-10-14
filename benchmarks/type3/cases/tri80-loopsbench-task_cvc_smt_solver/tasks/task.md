Build a Python SMT solver stack inspired by the CVC family. Work in
`/workspace`, treat `solver/` as the main implementation area, and extend the
existing base instead of replacing it. The starting framework already has a
basic CDCL SAT core plus QF_UF and QF_LIA support.

Implement the solver in three stages:

`CVC4 competition-solver layer`
Strengthen the SAT engine with two-watched-literal propagation, VSIDS with
phase saving, geometric restarts, and clause database reduction. Add the
missing theory support for bit-vectors, non-linear arithmetic, arrays, and
Nelson-Oppen combination.

`CVC4Sy SyGuS layer`
Add syntax-guided synthesis with a CEGIS loop, grammar-constrained
enumeration, equivalence pruning, grammar normalization, divide-and-conquer
decomposition, and multi-function synthesis support.

`cvc5-style feature layer`
Add proof production and checking in Alethe form, finite-set and sequence
theories, quantifier instantiation, incremental solving with `push`/`pop`,
and optimization modulo theories.

Keep the earlier logic support working while each stage lands. Benchmarks live
in `benchmarks/`; `python3 run_benchmarks.py` is part of the expected
workflow, and benchmark results must be printed as `KEY: VALUE` lines. Tests
verify the exposed benchmark-level correctness signals such as
`QF_BV_CORRECT`, `QF_NIA_CORRECT`, `SYGUS_LIA_CORRECT`, `QF_FS_CORRECT`, and
`PROOF_CORRECT`, so preserve those contracts while you extend the solver.
