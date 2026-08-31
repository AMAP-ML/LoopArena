# Compatibility issues with SciPy 1.14+

## Description
I am encountering issues when running CVXPY with the newly released SciPy 1.14.0.

Specifically, using atoms like `lambda_max` results in a `TypeError`. Additionally, I have observed `AttributeError`s related to sparse matrices in other parts of the codebase (e.g., during tests or internal reductions).

## Reproduction Script
The following script reproduces the `TypeError` when running with SciPy 1.14+:

```python
import cvxpy as cp
import numpy as np

# Create a simple symmetric matrix
A_val = np.eye(3)
A = cp.Parameter((3, 3), value=A_val)

# Use lambda_max which calls scipy.linalg.eigvalsh
expr = cp.lambda_max(A)

# Trigger the computation
print("Lambda max:", expr.value)
```

## Actual Behavior
```
TypeError: eigvalsh() got an unexpected keyword argument 'eigvals'
```

## Expected Behavior
The script should successfully calculate the maximum eigenvalue without error, supporting newer versions of SciPy.
