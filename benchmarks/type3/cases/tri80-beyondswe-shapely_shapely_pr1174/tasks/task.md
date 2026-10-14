# DeprecationWarning when assigning geometries to NumPy arrays in NumPy 1.21+

## Description
After upgrading the environment to use NumPy 1.21 or later, interactions between Shapely geometries and NumPy arrays have started emitting a `DeprecationWarning`. This issue specifically manifests when assigning geometry objects (such as `Polygon`) to NumPy object arrays.

## Current Behavior
When attempting to assign a Shapely geometry to a NumPy object array using NumPy 1.21+, the following warning is printed to stderr.

**Reproduction:**
```python
import numpy as np
from shapely.geometry import Polygon

# Ensure running with NumPy >= 1.21
p = Polygon([(0, 0), (1, 1), (1, 0)])

# Create an object array and assign the geometry
arr = np.empty(1, dtype=object)
arr[0] = p
```

**Output:**
```text
DeprecationWarning: An exception was ignored while fetching the attribute __array_interface__ from object
```

## Expected Behavior
The assignment of geometry objects to NumPy arrays should proceed silently without emitting `DeprecationWarning`, consistent with behavior in previous NumPy versions.

The repository is at `/workspace/shapely`, checked out at commit `9a761739aea19823fcead4391ef043edafa74db6`.