Implement a series of 6 search-based test generation algorithms as a Python framework.
Working directory: /workspace. The base `src/` package provides program model (CFG with
branches), test case representation, execution engine, genetic operators, and 5 benchmark
programs. Implement: (1) EvoSuite whole test suite GA with branch coverage fitness;
(2) MOSA many-objective sorting algorithm treating each branch as a separate objective
with Pareto non-dominated sorting, preference criterion, and crowding distance;
(3) Whole-Suite Archive GA with per-goal archiving, secondary objectives (test length,
exception diversity, mutation score), and post-search minimization pass;
(4) DynaMOSA with preference-based sorting, coverage archive, and dynamic target selection
via control dependency graph; (5) aDynaMOSA with 7 performance proxies, stagnation detection,
and adaptive heuristic switching; (6) EMOTE modular testing with relaxed prefix constraints
and selective coverage contribution. Each module's experiments/run_all.py outputs
ALGORITHM_METRIC: VALUE lines. Tests verify deterministic results with seed=42.
