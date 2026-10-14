You are given:
A raylib source tree at /workspace/raylib
A deterministic headless mini-game project at /workspace/game_project (Shapes2DReplay)
Replay and expected-output assets under /workspace/assets
Helper scripts under /workspace/scripts

Shapes2DReplay is a deterministic bootstrap task for raylib's 2D engine stack.
It now exercises a broader set of APIs than the original version:
multiple spline point-query families (linear, B-spline, Catmull-Rom, quadratic/cubic Bezier)
rectangle/circle/line/triangle/polygon collision helpers
static and dynamic 2D overlay rendering paths
CPU-side image generation, transformation, and drawing helpers from rtextures
frame-buffer and image-buffer pixel sampling used in the final aggregate state hash

Your task is to complete a full bootstrap workflow:

Phase A - Engine repair and verification
1) Repair the intentionally hollowed or broken parts of the 2D shapes/runtime stack.
   Several function families are missing at once; do not assume a single localized
   edit is sufficient.
2) Build raylib:
     /workspace/scripts/build_raylib.sh
3) Run focused engine checks:
     /workspace/scripts/run_engine_checks.sh

Phase B - Game repair and verification
4) Implement /workspace/game_project/replay_runner.c so replay execution produces
   correct outputs from the repaired spline/collision/rendering behavior.
5) Run the game twice with the same replay and seed:
     RUN_DIR=/workspace/output/replay_run_1 /workspace/scripts/run_headless_replay.sh
     RUN_DIR=/workspace/output/replay_run_2 /workspace/scripts/run_headless_replay.sh
6) Copy the first run as canonical game output:
     cp /workspace/output/replay_run_1/final_state.json /workspace/output/game_final_state.json
     cp /workspace/output/replay_run_1/final_state.sha256 /workspace/output/game_final_state.sha256
7) Write /workspace/output/determinism_report.json with:
     same_seed_hash_equal, run_1_hash, run_2_hash,
     run_1_event_lines, run_2_event_lines
     (derive hashes from your own replay outputs; no reference SHA256 is shipped in the workspace)

Required output files:
/workspace/output/engine_checks.json
/workspace/output/engine_test_stdout.log
/workspace/output/replay_run_1/final_state.json
/workspace/output/replay_run_1/final_state.sha256
/workspace/output/replay_run_1/shape_events.jsonl
/workspace/output/replay_run_1/engine_trace.json
/workspace/output/replay_run_2/final_state.json
/workspace/output/replay_run_2/final_state.sha256
/workspace/output/replay_run_2/shape_events.jsonl
/workspace/output/replay_run_2/engine_trace.json
/workspace/output/game_final_state.json
/workspace/output/game_final_state.sha256
/workspace/output/determinism_report.json

Rules:
Do not edit files under /tests.
Do not fabricate hashes or reports; derive them from real outputs.
Do not bypass the provided build/check/replay scripts.
Do not replace engine logic with mocks or stubs.
