# DeprecationWarning: .A attribute on sparse matrices is deprecated

## Description
When using `cvxpy` with `scipy` sparse matrices, I am encountering `DeprecationWarning`s. The warning indicates that the `.A` attribute on sparse matrices is deprecated and is scheduled for removal in SciPy 1.14.0. This issue needs to be addressed to prevent `AttributeError`s in future versions of SciPy.

## Reproduction Script
```python
import cvxpy as cp
import scipy.sparse as sp
import numpy as np
import warnings

# Trigger deprecation warnings
warnings.simplefilter('always')

# Create a sparse matrix
n = 3
sparse_mat = sp.eye(n, format='csc')

# Parameter with sparse value
P = cp.Parameter((n, n), value=sparse_mat)

# Operation involving sparse inputs
expr = cp.sum(P)

# Canonicalize to trigger the code path
prob = cp.Problem(cp.Minimize(expr))
prob.get_problem_data(cp.OSQP)
print("Script finished.")
```

## Actual Behavior
The script triggers a `DeprecationWarning` stating that the `.A` attribute on sparse matrices is deprecated.

## Expected Behavior
The library should handle sparse matrices without triggering deprecation warnings to ensure compatibility with future SciPy versions.

The repository is at `/workspace/cvxpy`, checked out at commit `3bde75e82e74924b6cc4cce84344e3cbb509d0e3`.