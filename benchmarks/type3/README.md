# Type III

Case directories use `tri80-<source>-<task>`.

80 complete tasks paired with Type II: 21 SCBench, 29 BeyondSWE, and 30 LoopsBench tasks. Runs begin at the official task origin. SCBench follows all native checkpoints; BeyondSWE and LoopsBench execute their complete tasks.

`CASE_INDEX.json` identifies the cases. Case manifests bind task sequences, evaluator plans, source identities, and budgets. Large upstream source trees and images are obtained separately.

Follow [setup and execution](../../docs/setup.md). Success requires the declared terminal evaluations, valid protocol, and compliance with the Worker budget.
