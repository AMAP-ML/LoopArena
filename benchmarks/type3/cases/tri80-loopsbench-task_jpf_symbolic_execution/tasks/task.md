Build a Java verification stack based on JPF-style model checking and symbolic
execution. The workspace already contains the multi-project layout
(`jpf-core`, `jpf-symbc`, `jpf-shadow-plus`, `SootConnection`), the Ant build
files, the solver dependencies, and the example programs. Your job is to make
the three layers work together:

`jpf_core`
Implement the core model checker: the custom JVM, state management, DFS-style
exploration, and property-checking flow.

`symbolic_pathfinder`
Build the symbolic-execution layer on top of that core, including the custom
bytecode factory, path-condition management, and solver integration.

`shadow_jpf_plus`
Add shadow symbolic execution for regression testing, including
`DiffExpression`, the four-way execution splits, and the experiment runner.

Preserve the expected key entry points such as `gov.nasa.jpf.JPF`,
`gov.nasa.jpf.symbc.SymbolicInstructionFactory`,
`gov.nasa.jpf.shadow.ShadowInstructionFactory`, and
`gov.nasa.jpf.shadow.RunnerShadowPlus`. Each module has a `run.sh` that builds
and runs its experiments, and the harness validates the reported
`EXPERIMENT_NAME: VALUE` results within tolerance, so keep those experiment
contracts intact while you implement the stack.
