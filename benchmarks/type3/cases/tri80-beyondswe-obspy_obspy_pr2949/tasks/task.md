# AttributeError: module 'numpy' has no attribute 'complex' in obspy

## Description
I am attempting to use `obspy` in an environment with a recent version of NumPy (1.24+). When running my scripts, the application crashes with an `AttributeError`.

The traceback indicates that `numpy` has no attribute `complex`. This seems to happen in `obspy.signal.tf_misfit.cwt`.

Additionally, I have noticed various deprecation warnings being emitted regarding NumPy usage (such as matrix objects and type aliases) when using versions of NumPy >= 1.20.

## Reproduction Script
```python
import numpy as np
from obspy.signal.tf_misfit import cwt

# Create dummy signal data
st = np.random.rand(100)
dt = 0.01
w0 = 6
fmin = 1.0
fmax = 10.0

# This triggers the AttributeError on NumPy >= 1.24
cwt(st, dt, w0, fmin, fmax)
```

## Actual Behavior
```
Traceback (most recent call last):
  File "reproduce_issue.py", line 12, in <module>
    cwt(st, dt, w0, fmin, fmax)
  File "/workspace/obspy/obspy/signal/tf_misfit.py", line 60, in cwt
    cwt = np.zeros((npts // 2, nf), dtype=np.complex)
                                          ^^^^^^^^^^
  File "/usr/local/lib/python3.11/site-packages/numpy/__init__.py", line 319, in __getattr__
    raise AttributeError(__former_attrs__[attr])
AttributeError: module 'numpy' has no attribute 'complex'.
```

## Expected Behavior
The code should execute successfully on modern NumPy versions without raising `AttributeError` or emitting deprecation warnings related to NumPy compatibility.

The repository is at `/workspace/obspy`, checked out at commit `5b7252c2ec3d04fa6c9fdaafbc0afba6e05a2573`.