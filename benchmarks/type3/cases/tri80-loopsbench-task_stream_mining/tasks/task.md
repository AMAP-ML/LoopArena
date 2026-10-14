Build an online stream-mining stack in Python on top of the components already
present in `/workspace`: the stream generators, prequential evaluation
utilities, base classifier classes, and attribute observers.

Implement the stack in dependency order:

`VFDT`
A Hoeffding-tree learner for incremental decision-tree training and prediction.

`CVFDT`
A concept-drift-aware extension of VFDT with sliding-window management and
background replacement logic.

`ADWIN`
An adaptive windowing drift detector with the expected change-detection and
estimation behavior.

`MOA-style ensemble layer`
Add OzaBagging plus the interleaved test-then-train evaluation runner so the
online learners can be benchmarked the way the task expects.

`SAM-KNN`
Add the self-adjusting-memory kNN model with STM/LTM behavior for non-tree
stream learning.

Keep the public entry points the tests exercise, including:
`HoeffdingTree.train/predict/get_tree_size`,
`CVFDT.train/predict`,
`ADWIN.add_element/detected_change/get_estimation`,
`OzaBagging.train/predict`,
`EvaluateInterleavedTestThenTrain.run`,
and `SAMModel.partial_fit/predict`.

Use `/workspace/requirements/` for the exact algorithmic contracts and
experiment definitions. `run.sh` must execute the required experiments and
print one `EXPERIMENT_NAME: VALUE` line per reported metric.
