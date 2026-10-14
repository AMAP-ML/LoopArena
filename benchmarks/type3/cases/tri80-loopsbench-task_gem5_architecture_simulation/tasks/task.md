Build a Python architecture-simulation stack inspired by gem5. Work in
`/workspace`, and reuse the existing event-driven simulator (`sim_engine.py`)
and deterministic workloads (`workloads.py`) as the base rather than starting
over.

Implement the system in four dependent layers:

`gem5`
Build the core CPU and memory simulator: the simple CPUs, the out-of-order
CPU pipeline, caches and coherence behavior, the detailed DRAM controller, and
the syscall-emulation runner.

`gem5_v20`
Extend that base with the stdlib-style board/processor/cache-hierarchy API,
stronger branch prediction, modern memory controllers, hierarchical stats,
checkpoint support, and the full-system pieces required by the tests.

`gem5_aladdin`
Add accelerator co-design support: dependence-graph construction, scheduling,
loop and DMA analysis, scratchpad and cache-backed accelerator models, MMIO
control, and the power/EDP evaluation path.

`gem5_salam`
Finish with the LLVM-based accelerator flow: IR parsing, CDFG construction,
liveness and resource analysis, multiple scheduling strategies, and the
per-cycle hardware accelerator simulation stack.

Keep each layer as an extension of the earlier simulator rather than a
disconnected rewrite. Use `requirements/` for exact contracts and experiment
expectations. `run.sh` must execute the required experiments and report
`EXPERIMENT_NAME: VALUE` lines for the harness.
