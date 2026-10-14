Build a Python static-analysis stack for Java-like programs in `/workspace`.
The repository already contains the shared program model, utilities,
benchmarks, and `run.sh`; extend that base through three dependent layers:

`soot_analysis.py`
Build the Jimple-like IR and the core whole-program analysis utilities,
including CHA call-graph construction, the scene registry, copy propagation,
and dead-code elimination.

`flowdroid_analysis.py`
Add the FlowDroid-style IFDS taint analysis on top of that IR and call graph,
including access paths, taint facts, alias support, source/sink handling, and
lifecycle-aware propagation.

`stubdroid_analysis.py`
Finish with automatic library-summary generation plus the summary-aware taint
wrapper and analysis layer.

Each module should still run standalone via `python3 <module>.py`, and
`run.sh` must execute all three layers with `PYTHONHASHSEED=0` while emitting
the required `KEY: VALUE` experiment lines. Use `requirements/` as the source
of truth for the detailed contracts.
