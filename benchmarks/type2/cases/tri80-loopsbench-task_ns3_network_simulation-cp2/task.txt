Extend the ns-3 codebase in `/workspace` with two radio-access simulation
layers. The repository already contains the core simulator, network stack,
mobility support, propagation code, and the CMake-based build setup. Your job
is to add the missing modules in dependency order:

`src/lte/`
Build the LTE-LENA layer with the EPC path, the PHY/MAC/RLC/PDCP/RRC protocol
stack, scheduler support, and the expected trace outputs.

`src/mmwave/`
Build the 5G mmWave extension on top of that LTE base, including the
mmWave-specific PHY details, channel models, beamforming behavior, and the
flexible-TTI scheduler logic.

Keep the build flow compatible with the existing CMake setup:
`cd /workspace && cmake -S . -B build && cmake --build build -j$(nproc)`.
`run.sh` must build the simulator, run the required reference experiments, and
emit `EXPERIMENT_NAME: VALUE` lines for the harness. Use `requirements/` as
the source of truth for exact module behavior and metrics.
