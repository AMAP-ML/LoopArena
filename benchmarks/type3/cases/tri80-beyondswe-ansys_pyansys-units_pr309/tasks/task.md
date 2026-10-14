# [Feature] Configurable dimensions for angular quantities

## Description
We need to implement a mechanism to treat angular quantities as fundamental dimensions based on configuration, as requested in [Issue #307](https://github.com/ansys/pyansys-units/issues/307).

Currently, angular units are handled statically. To support specific dimensional analysis workflows while maintaining backward compatibility for scalar operations, we need to make this behavior configurable via an environment variable.

Please implement the configuration mechanism proposed in the linked issue.

## Steps to Reproduce / Logs
The following script demonstrates the current behavior where dimensions are static:

```python
import ansys.units as pyunits
import os

# Check default behavior
q = pyunits.Quantity(1.0, "degree")
print(f"Dimensions (Default): {q.dimensions}")

# TODO: We need to enable the configuration described in Issue #307
# os.environ["<VAR_NAME_FROM_ISSUE>"] = "1"

q_configured = pyunits.Quantity(1.0, "degree")
print(f"Dimensions (With Env Var): {q_configured.dimensions}")
```

## Expected Behavior
1. By default, the behavior should remain backward compatible (dimensions are empty).
2. When the environment variable specified in [Issue #307](https://github.com/ansys/pyansys-units/issues/307) is set, the dimensions listed in the issue should be registered.

The repository is at `/workspace/pyansys-units`, checked out at commit `6780b642bb16442584968215ead84eda13e1fc20`.