Build a Python binary-analysis pipeline on top of angr. Work in the existing
workspace, use the binaries in `test_binaries/`, and fill in the package
skeletons that are already present. The modules depend on each other, so
implement them in this order:

`angr_analysis/analyzer.py`
Core program analysis: CFG recovery, function discovery, and symbolic path
exploration.

`driller_lite/driller_main.py`
Selective symbolic execution that starts from concrete inputs and generates new
inputs able to cross guarded boundaries such as magic-number checks.

`cozy_lite/analyzer.py`
Comparative symbolic execution for pre- and post-patch binaries so the tool can
surface behavioral differences and changed terminal states.

`taint_analysis/taint_tracker.py`
Forward taint propagation through registers and memory, with reporting when
user-controlled data reaches dangerous sinks such as `memcpy`, `printf`, or
`system`.

`vuln_detector/scanner.py`
Constraint-driven vulnerability scanning for buffer overflows, format-string
bugs, and use-after-free conditions, including generation of concrete trigger
inputs.

Preserve the public entry points the tests call:
`analyze_binary(binary_path) -> dict`,
`DrillerLite(binary, input_bytes).drill() -> list[bytes]`,
`CozyAnalysis(binary_pre, binary_post, input_size).run() -> dict`,
`TaintAnalysis(binary_path).analyze() -> dict`,
and `VulnScanner(binary_path).scan() -> list[VulnReport]`.

Use `requirements/<slug>.yaml` for the exact per-module contracts. Keep
`run.sh` up to date so it runs the required experiments and prints `KEY: VALUE`
lines for the harness.
