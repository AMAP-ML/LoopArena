Build a lightweight Python ML toolkit called `minilearn` in `/workspace`. The
base package already provides shared helpers such as `check_random_state`,
`check_array`, and `euclidean_distances`; extend that foundation in two layers:

`Scikit-learn core`
Implement the estimator API, preprocessing, decomposition, clustering,
neighbors, linear models, pipelines, model selection, metrics, and synthetic
dataset generators listed in the source tree. The result should feel like a
compact scikit-learn-style stack rather than a bag of unrelated utilities.

`Imbalanced-learn layer`
Add the resampling and imbalanced-classification pieces on top of that core:
the sampler base class, over/under-sampling methods, SMOTE-family combiners,
sampler-aware pipelines, and the imbalance-focused metrics.

Preserve deterministic behavior with `seed=42`, because the tests depend on
repeatable outputs. Each module has an experiment script that must continue to
emit `EXPERIMENT_NAME: VALUE` lines for the harness. Use `requirements/` for
the exact API and metric contracts.
