# Remove support for deprecated `numpy.matrix`

## Description
The `numpy.matrix` class is deprecated in the NumPy ecosystem and its usage is discouraged. We need to modernize the library to use `numpy.ndarray` exclusively.

This task involves removing all support for `numpy.matrix` from the library. This includes removing configuration options that enable matrix output and dropping support for legacy input formats that rely on matrix-style parsing.

## Reproduction Script
The following script demonstrates the legacy features that are currently supported but need to be removed or updated.

```python
import control as ct
import numpy as np

# Currently, users can initialize systems using MATLAB-style strings.
sys = ct.ss("1. -2; 3. -4", "5.; 7", "6. 8", "9.")
print(f"System created via string. State matrix type: {type(sys.A)}")

# Users can currently configure the library to return np.matrix objects
try:
    ct.use_numpy_matrix(True)
    sys_mat = ct.ss([[1, -2], [3, -4]], [[5], [7]], [[6, 8]], 9)
    print(f"Configured output type: {type(sys_mat.A)}")
except AttributeError:
    print("use_numpy_matrix is unavailable.")
```

## Expected Behavior
The library should standardize on `numpy.ndarray`. Configuration options enabling `numpy.matrix` should be removed, and the legacy string input format demonstrated above should no longer be supported.

The repository is at `/workspace/python-control`, checked out at commit `71bbce26d65aaf9509410a0ccdff2573e7a705ad`.