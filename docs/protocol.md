# LoopArena protocol

LoopArena evaluates models in the Controller role as they guide a separate,
fixed coding Worker through long-horizon tasks. Controller models use the same
Worker, task, starting workspace, public conversation, coding tools, budget,
runtime, and terminal evaluator. Type II and Type III also report shared
no-control and fixed-control reference policies; these provide context for the
Controller results and are excluded from the Controller-model ranking.

## Arms

In the **no-control** arm, the Worker receives one instruction to complete the
task and runs until it finishes or exhausts its budget.

In the **fixed-control** arm, the Worker repeatedly receives the same task-level
goal at loop handoffs. No model Controller reviews the run or adapts the
instruction; the policy stops after the Worker explicitly reports that the goal
is complete.

In the **controlled** arm:

1. the Worker receives a bounded assignment;
2. when that assignment ends, a fresh Reporter reads the observable Worker
   conversation and inspects the current workspace with read-only tools;
3. the Reporter produces a structured progress report with citations to the
   Worker conversation;
4. the deterministic packet compiler combines that report with the cited
   Worker turns and remaining budget;
5. the Controller returns `advance`, `verify`, or `stop`;
6. an `advance` or `verify` instruction continues the same Worker conversation,
   while `stop` ends the episode.

No-control has no Reporter, Controller, packet compilation, or periodic
interruption.

## Information boundary

The Worker sees the public task, public conversation, current Controller
instruction, ordinary coding tools, and the results of its own tool calls. It
does not see private evaluator inputs or results.

The Reporter sees the public task, the complete observable Worker conversation,
and a read-only view of the current workspace. It cannot edit files or execute
commands.

The Controller sees the Reporter report, the Worker turns cited by that report,
the remaining budget, and its own earlier reports and decisions. It cannot
inspect the repository or private evaluator directly.

## Decisions

- `advance` assigns a bounded implementation, debugging, or cleanup step.
- `verify` assigns evidence collection. It may use the ordinary coding tools
  but must not intentionally change production behavior.
- `stop` ends the episode when the available evidence supports completion.

Controller output is a JSON object containing the decision, rationale,
Worker instruction, protected invariants, and verification acceptance
condition. Invalid output is recorded as a model outcome rather than retried
into a different decision.

## Budgets

The main Worker has 600 model turns in either arm. Each Reporter call has at
most 50 turns. A controlled run has at most 128 control cycles and 24 hours of
Controller-channel wall time. Dataset runners may impose a shorter overall
wall-time limit when the source task requires one.

One Worker response may make at most one tool call. Tool output and model input
are bounded by the same deterministic context policy in both arms. Context
compaction changes representation only when a provider input would exceed the
declared capacity; the run records when it occurs.

## Evaluation

Model access ends before terminal evaluation. SCBench evaluates a sealed copy
of the final workspace after the solve container stops. BeyondSWE runs its
source-native verifier at the final model boundary in the live source
container, then attaches the evaluator receipt to the sealed solve result.

The terminal evaluator determines task success. A valid evaluator failure is
a model result. Provider, runner, container, or evaluator infrastructure
failures are recorded separately and are not converted into task failures.

Type II restores its frozen starting workspace and runs one declared task
increment. Type III starts from the official problem origin; SCBench executes
every native checkpoint in order, while BeyondSWE executes its complete task.

## Run artifacts

Each run records the observable Worker transcript, per-slice activity,
Reporter outputs, Controller inputs and outputs, token usage, runtime identity,
and terminal evaluator receipt. `solve_manifest.json` seals the state produced
by the models; `run_manifest.json` adds the terminal evaluation result.

Provider-interrupted runs may resume only from the last durable model boundary.
Completed model work is not replayed. The resume record binds the task,
workspace, arm, seed, model configuration, and previous progress needed to
continue the same experiment.
