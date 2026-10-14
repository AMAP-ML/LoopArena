You are given:
A raylib source tree at /workspace/raylib
A deterministic headless mini-game project at /workspace/game_project (TexturesReplay)
Replay and expected-output assets under /workspace/assets
Helper scripts under /workspace/scripts

TexturesReplay is a hard bootstrap task over raylib's textures stack.
It now exercises:
procedural CPU image generation
pixel-format conversion and internal pixel-storage helpers
color conversion/blending utility functions
in-place CPU image transforms and palette access
CPU-side image drawing
GPU texture upload, render-texture drawing, and screen readback
a deterministic multi-tick replay harness that folds both CPU and GPU probes into a final hash

Your task is to complete a full bootstrap workflow:

Phase A - Engine repair and verification
1) Complete intentionally hollowed textures module implementation. Multiple
   function families are missing or broken at once: image I/O, generation,
   color utilities, pixel-format helpers, CPU transforms, CPU drawing, GPU
   texture objects, and the replay-facing harness glue. Validate behavior
   end-to-end instead of assuming a single local edit is enough.
2) Build raylib:
     /workspace/scripts/build_raylib.sh
3) Run focused engine checks:
     /workspace/scripts/run_engine_checks.sh

Phase B - Game repair and verification
4) Implement /workspace/game_project/replay_runner.c so replay execution produces
   correct outputs from real textures-module behavior.
5) Run the game twice with the same replay and seed:
     RUN_DIR=/workspace/output/replay_run_1 /workspace/scripts/run_headless_replay.sh
     RUN_DIR=/workspace/output/replay_run_2 /workspace/scripts/run_headless_replay.sh
6) Copy the first run as canonical game output:
     cp /workspace/output/replay_run_1/final_state.json /workspace/output/game_final_state.json
     cp /workspace/output/replay_run_1/final_state.sha256 /workspace/output/game_final_state.sha256
7) Write /workspace/output/determinism_report.json with:
     same_seed_hash_equal, run_1_hash, run_2_hash,
     run_1_event_lines, run_2_event_lines
     (derive hashes from your own replay outputs; no reference SHA256 is shipped in the workspace.)

Required output files:
/workspace/output/engine_checks.json
/workspace/output/engine_test_stdout.log
/workspace/output/replay_run_1/final_state.json
/workspace/output/replay_run_1/final_state.sha256
/workspace/output/replay_run_1/texture_events.jsonl
/workspace/output/replay_run_1/engine_trace.json
/workspace/output/replay_run_2/final_state.json
/workspace/output/replay_run_2/final_state.sha256
/workspace/output/replay_run_2/texture_events.jsonl
/workspace/output/replay_run_2/engine_trace.json
/workspace/output/game_final_state.json
/workspace/output/game_final_state.sha256
/workspace/output/determinism_report.json

Rules:
Do not edit files under /tests.
Do not fabricate hashes; compute them from the actual JSON outputs you generate.
Do not bypass engine build/check scripts.
Do not replace textures-module logic with mocks or stubs.
