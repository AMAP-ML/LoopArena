# TypeError when reading SeisComp XML with extra whitespace in FIR coefficients

## Description
When reading a SeisComp XML (`sc3ml` / `scxml`) file, if the `coefficients` tag in a `responseFIR` element contains leading or trailing whitespace, `read_inventory` fails with a `TypeError`.

## Reproduction Script
```python
from obspy import read_inventory
import warnings

# Use simplefilter to ensure warnings don't hide the root cause if any
warnings.simplefilter('always')

xml_content = """<?xml version="1.0"?>
<seiscomp xmlns="http://geofon.gfz-potsdam.de/ns/seiscomp3-schema/0.7" version="0.7">
 <Inventory>
  <datalogger publicID="DL1" name="DL1">
   <decimation sampleRateNumerator="1" sampleRateDenominator="1">
    <digitalFilterChain>FIR1</digitalFilterChain>
   </decimation>
  </datalogger>
  <responseFIR publicID="FIR1" name="FIR1">
   <gain>1.0</gain>
   <decimationFactor>1</decimationFactor>
   <delay>0.0</delay>
   <correction>0.0</correction>
   <numberOfCoefficients>2</numberOfCoefficients>
   <symmetry>A</symmetry>
   <coefficients> 0.1 0.2 </coefficients>
  </responseFIR>
 </Inventory>
</seiscomp>
"""

with open("repro_whitespace.scxml", "w") as f:
    f.write(xml_content)

print("Attempting to read inventory...")
read_inventory("repro_whitespace.scxml")
print("Success")
```

## Actual Behavior
The script crashes with the following error (traceback abbreviated):
```
TypeError: float() argument must be a string or a real number, not 'NoneType'
```
(Note: `UserWarning`s about failing to convert values to float might also be printed).

## Expected Behavior
The parser should ignore leading/trailing whitespace in the `coefficients` string and successfully parse the valid coefficients.

The repository is at `/workspace/obspy`, checked out at commit `6ce06ad8a23abe31e04b77a9cbd3ce33d7715d43`.