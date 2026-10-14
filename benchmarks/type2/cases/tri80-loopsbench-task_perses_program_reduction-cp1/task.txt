Finish the program-reduction framework in `/workspace`. The tokenizer,
parser, SparTree, property tester, ddmin baseline, and reduction driver are
already present. What is missing is the sequence of reducers and experiment
wiring that turns the repo into a working Perses-family benchmark.

Build the project in dependency order: restore the core Perses path first,
then layer Vulcan 1-minimality, PPR, LPR, and Sivand on top. Later phases
depend on earlier ones, so do not leave them as isolated demos.

`reduction/pnf.py` and `reduction/perses_reducer.py`
Start by making the base Perses reducer work. `pnf.py` must normalize
grammars into the expected prioritized normal form, including unreachable-rule
elimination, single-alternative inlining, recursion removal, and quantified
rule handling. `perses_reducer.py` must perform syntax-guided reduction over
the SparTree, including ddmin over Kleene children and compatible-subtree
replacement. At this point `run.sh` should already be able to run the DD
baseline and the Perses experiments.

`reduction/vulcan/`
Add identifier replacement, compatible subtree replacement, and the local
exhaustive deletion pass used for 1-minimality, then extend `run.sh` with the
Vulcan experiments.

`ppr/`
Restore the pairwise-reduction pipeline across the seed/variant inputs:
diff utilities, MinTdiff, MinLdiff, MinCommonality, result handling, and the
PPR driver.

`reduction/lpr/`
Implement the deterministic mock-LLM transformations and the concrete LLM
reducers that use them.

`reduction/sivand/`
Restore the model oracle, attention-guided prioritization, Perses-style
reducer, attribution helpers, and explanation-finding pipeline used for the
Sivand experiment.

`run.sh` is part of the task. It must execute the required experiments for
`bug1`, `bug2`, `bug3`, and `ppr_bug1`, including the LPR and Sivand runs,
using only the Python 3 standard library. Preserve the existing module names
and wire the pieces together into one working framework.
