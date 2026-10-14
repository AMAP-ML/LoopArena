# Type II

Case directories use `tri80-<source>-<task>-cp<N>`.

80 frozen continuation tasks paired with Type III: 21 SCBench, 29 BeyondSWE, and 30 LoopsBench tasks. The Controller chooses a complete contract from the provided public state, then the Worker continues from the frozen workspace.

`CASE_INDEX.json` identifies the cases. Each case contains its task, public messages, evaluator plan and a workspace reconstruction recipe. Small overlays contain only files needed in addition to pinned upstream sources. Full workspace archives are generated locally outside this repository.

Follow [setup and execution](../../docs/setup.md). Success requires protocol and budget validity in addition to terminal test success.
