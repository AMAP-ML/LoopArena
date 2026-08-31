# [Feature Request] Add native support for Python `enum.Enum` objects

## Description
Currently, `colander` does not provide a built-in type for handling Python's standard `enum.Enum` objects. Users generally have to write custom types to integrate Enums into schemas.

We need to introduce a native `colander.Enum` type to address this gap. The implementation should be robust and consider use cases discussed in the community.

Relevant upstream discussion: [#301](https://github.com/Pylons/colander/issues/301).

## Steps to Reproduce / Logs
The following code demonstrates the desired usage API, which currently fails because `colander.Enum` does not exist.

```python
import colander
import enum
import unittest

class Color(enum.Enum):
    RED = 1
    GREEN = 2
    BLUE = 3

class Schema(colander.MappingSchema):
    # Desired API: Validation by Name
    color_name = colander.Enum(Color)

    # Desired API: Validation by Value
    color_val = colander.Enum(Color, by_value=True)

schema = Schema()

# Test Case: Current Failure
try:
    # This should deserialize 'RED' to Color.RED and 2 to Color.GREEN
    data = {'color_name': 'RED', 'color_val': 2}
    result = schema.deserialize(data)
    print("Success:", result)
except AttributeError as e:
    print("Failed as expected:", e)
except colander.Invalid as e:
    print("Validation Error:", e)
```

**Current Output:**
```
AttributeError: module 'colander' has no attribute 'Enum'
```

## Expected Behavior
The `colander` library should expose a new `Enum` type that satisfies the usage demonstrated above. It should handle serialization and deserialization correctly for the configured modes and raise `colander.Invalid` for invalid inputs.