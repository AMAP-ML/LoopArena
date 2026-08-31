# TypeError in `cp.reshape` with NumPy 2.1.0

## Description
After updating to NumPy 2.1.0, I am encountering a `TypeError` when using `cvxpy.reshape`. The error occurs specifically when attempting to evaluate the expression (accessing `.value`). This appears to be a regression or incompatibility with the new NumPy version, as the code works correctly on previous versions.

## Reproduction Script
```python
import cvxpy as cp
import numpy as np

# This script fails on NumPy 2.1.0
x = cp.Variable(4)
x.value = np.array([1, 2, 3, 4])

# Creating a reshape expression
reshaped_expr = cp.reshape(x, (2, 2))

# This line triggers the bug
try:
    print(reshaped_expr.value)
except TypeError as e:
    print(f"Caught error: {e}")
```

## Actual Behavior
The code raises a `TypeError` indicating an issue with the arguments passed to `reshape`:

```
TypeError: reshape() takes from 1 to 2 positional arguments but 3 were given
```

## Expected Behavior
The `reshape` atom should correctly evaluate and return the reshaped array without raising a `TypeError`, consistent with behavior in older NumPy versions.