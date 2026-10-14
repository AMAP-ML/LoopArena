You are given:
A MonoGame source tree at /workspace/MonoGame
A deterministic headless mini-game project at /workspace/game_project (Transform Arena)
Replay and expected-output assets under /workspace/assets

Your task is to complete a full bootstrap workflow:

Phase A - Engine and game repair, then verification
1) Complete intentionally hollowed Microsoft XNA Framework 3D transform math
   (Vector3 and Matrix public API surface used by the replay). The task contains
   large missing implementations. The existing code around the hollowed sections may
   also have potential correctness issues. You must retrieve code context across
   modules and restore correct behavior end-to-end.
2) Complete the game-side C# project under /workspace/game_project (replay runner, replay
   I/O, simulation world). Together they are on the order of several hundred lines.
   The game is a partial skeleton with an intentional seed mismatch in the replay
   runner; final behavior must be deterministic and driven by Vector3 and Matrix
   operations, not hard-coded end states.
3) Build the MonoGame DesktopGL framework and test host, then run a selected set of
   MonoGame framework unit tests and save a machine-readable report.

Phase B - Game replay verification
4) Run the headless mini-game replay twice using the same seed and replay input.
   The game must exercise 3D vector math, matrix-vector transforms, 3D distance
   queries, and Point-to-Vector2 conversion on the same call chain as the hollowed
   engine code.
5) Export final state JSON and its SHA256 hash for each run.
6) Confirm replay determinism (two same-seed runs produce identical state hash).

Phase C - Integrated acceptance
7) Produce all required output artifacts listed below.
8) Ensure outputs match expected integrity checks.

Required output files:
/workspace/output/engine_checks.json
/workspace/output/engine_test_stdout.log
/workspace/output/replay_run_1/final_state.json
/workspace/output/replay_run_1/final_state.sha256
/workspace/output/replay_run_1/math_events.jsonl
/workspace/output/replay_run_1/engine_trace.json
/workspace/output/replay_run_2/final_state.json
/workspace/output/replay_run_2/final_state.sha256
/workspace/output/replay_run_2/math_events.jsonl
/workspace/output/replay_run_2/engine_trace.json
/workspace/output/game_final_state.json
/workspace/output/game_final_state.sha256
/workspace/output/determinism_report.json

Rules:
Automated grading (pytest) checks engine_checks.json, math event types, engine trace
  flags, replay determinism, and compares the run SHA256 to a reference value stored only under /tests
  (not in the agent workspace).
Do not edit files under /tests.
Do not bypass replay execution.
Do not fabricate hashes; compute them from the actual JSON outputs you generate.
The final hash must be computed from canonicalized JSON content.
Do not replace vector- and matrix-driven simulation with manual state mutation.
Do not edit files under /workspace/game_project/tests (if present).
Do not bypass engine-side missing implementations by stubbing outputs.
Engine fixes must be made on the 3D math types used by the game.
