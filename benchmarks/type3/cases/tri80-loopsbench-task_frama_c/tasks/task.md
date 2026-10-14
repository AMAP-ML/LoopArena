Build a small Frama-C-inspired static-analysis platform in `/workspace`. This
task has three layers that should fit together into one usable toolchain:

`frama_c`
A C front end built on `pycparser`, including CFG construction and the shared
analysis framework infrastructure, along with the fake libc support needed by
the tests.

`eva`
An abstract-interpretation layer with interval analysis, fixpoint iteration,
and alarm generation for suspicious program states.

`frama_c_sv`
An SV-COMP-style verification adapter that can parse properties and perform
the reachability checks expected by the benchmark harness.

Keep the modules connected as one analysis stack instead of treating them as
isolated scripts. Use `requirements/` for the exact per-module behavior, and
make sure the existing `pytest /tests/<module>/test_outputs.py` flow remains
the source of truth for success.
