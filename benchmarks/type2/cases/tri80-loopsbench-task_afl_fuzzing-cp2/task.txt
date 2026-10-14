Implement a series of 5 coverage-guided greybox fuzzing algorithms as a Python simulation framework.
Working directory: /workspace. The base `fuzzsim/` package provides program model, coverage bitmap,
mutation operators, and simulated target programs. Implement: (1) AFL core fuzzer with full deterministic
stages (bitflip/arith/interest/dict), havoc, splice, queue management with calibration and trimming,
corpus culling via greedy set cover, crash deduplication, fork server simulation, and constant power
schedule; (2) AFLFast Markov chain power schedules (FAST, COE, EXPLORE, EXPLOIT, LINEAR, QUAD) with
path frequency tracking, state transition modeling, and seed prioritization strategies; (3) AFLGo
directed fuzzing with call-graph and CFG distance computation (harmonic mean), distance normalization,
simulated annealing with multiple cooling schedules (exponential/linear/logarithmic/adaptive), and
distance-aware seed prioritization; (4) FairFuzz rare branch targeting with dynamic threshold
computation, probe-based influence detection, mutation mask generation with caching, and targeted
havoc preserving influential bytes; (5) AFL++ custom mutator API (init/fuzz/havoc_mutation/
post_process/trim/queue_new_entry), MutatorRegistry with dynamic weights, GrammarMutator,
CmpLog/RedQueen mutator, MOpt PSO-based mutation scheduling (pilot/core phases with velocity/position
updates), and PowerScheduleEnsemble rotation.
Each module's run.sh section outputs EXPERIMENT_NAME: VALUE lines. Tests verify deterministic results
with seed=42.
