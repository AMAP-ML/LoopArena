# Support for 1D Harris Sheet Equilibrium

## Description
I am trying to simulate a 1D Harris Sheet configuration using PlasmaPy, but I cannot find an implementation for it. The Harris Sheet is a standard equilibrium solution often used in reconnection studies.

I would like to request a new class to represent this equilibrium. It should allow initialization with the sheet's standard parameters (asymptotic magnetic field $B_0$, thickness $\delta$, and background pressure $P_0$) and provide methods to calculate the magnetic field, current density, and plasma pressure at a given position.

## Reproduction Script
Here is a script demonstrating the desired API and usage:

```python
import astropy.units as u

# Attempting to import a Harris Sheet class (hypothetical location)
# Currently this fails as the functionality is missing.
from plasmapy.plasma.equilibria1d import HarrisSheet

# Initialize with asymptotic B-field, thickness, and background pressure
hs = HarrisSheet(B0=1 * u.T, delta=1 * u.m, P0=10 * u.Pa)

y_pos = 0.5 * u.m
print(f"B at {y_pos}: {hs.magnetic_field(y_pos)}")
print(f"J at {y_pos}: {hs.current_density(y_pos)}")
print(f"P at {y_pos}: {hs.plasma_pressure(y_pos)}")
```

## Actual Behavior
```
ModuleNotFoundError: No module named 'plasmapy.plasma.equilibria1d'
```

## Expected Behavior
The `HarrisSheet` class should be available, and the script should output the calculated physical values for the magnetic field, current density, and pressure at `y = 0.5 m`.

The repository is at `/workspace/plasmapy`, checked out at commit `562bfe32ef23a64ca1fcc6397df7a19eed21bd23`.