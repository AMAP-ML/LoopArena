# convert_gwp fails with DimensionalityError for mass-per-energy units (e.g., kg/MJ)

## Description
I am attempting to use `convert_gwp` to convert greenhouse gas masses expressed per unit energy (specifically `kg / MJ`), but the function raises a `pint.DimensionalityError`.

Currently, the function works correctly for standard mass units (e.g., `t`) and mass rates (e.g., `Mt / a`), but it fails when processing emissions intensities commonly used in energy sector analysis.

This issue may be related to discussions in [Issue #32](https://github.com/IAMconsortium/units/issues/32).

## Steps to Reproduce / Logs
The following script reproduces the crash:

```python
from iam_units import registry, convert_gwp

# Create a quantity with mass per energy units: 5 kg per megajoule
qty = registry.Quantity(5.0, "kg / MJ")

print(f"Input: {qty}")

# Attempt to convert from CH4 to CO2 using AR5GWP100 metric
try:
    result = convert_gwp("AR5GWP100", qty, "CH4", "CO2")
    print("Conversion succeeded:", result)
except Exception as e:
    print(f"Error: {e}")
    # Re-raising to show stack trace if needed
    raise
```

**Actual Output:**
```text
pint.errors.DimensionalityError: Cannot convert from 'kilogram / megajoule' ([time] ** 2 / [length] ** 2) to '_gwp * kilogram / megajoule' ([_GWP] * [time] ** 2 / [length] ** 2)
```

## Expected Behavior
The conversion should succeed and return a Quantity with units equivalent to `kg_CO2 / MJ` (scaled by the appropriate Global Warming Potential factor). For example, converting `5.0 kg CH4 / MJ` to CO2 using a GWP of 28 should yield `140.0 kg CO2 / MJ`.

The repository is at `/workspace/units`, checked out at commit `02d9bc573b55a7eac00ef6410d0ffda2e0a1ba55`.