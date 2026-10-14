# Support reusing existing Pyramid routes in Cornice Services

## Description
Cornice currently abstracts route creation by automatically generating a new Pyramid route based on a provided `path`. However, this abstraction prevents users from utilizing advanced Pyramid routing features, such as custom [pregenerators](https://docs.pylonsproject.org/projects/pyramid/en/latest/api/interfaces.html#pyramid.interfaces.IRoutePregenerator) or [predicates](https://docs.pylonsproject.org/projects/pyramid/en/latest/glossary.html#term-route-predicate), which often require the route to be defined explicitly via `config.add_route`.

Users need the ability to attach a Cornice `Service` or `@resource` to a pre-existing route name, rather than being forced to define a new path.

Relevant discussions and context can be found in **[#137](https://github.com/Cornices/cornice/issues/137)** and **[#473](https://github.com/Cornices/cornice/issues/473)**.

## Steps to Reproduce / Logs
The following test case demonstrates the desired usage pattern. The user defines a route explicitly in the Pyramid config and attempts to bind a Cornice Service to it using a `pyramid_route` argument.

Currently, this fails because the `Service` class does not support this argument and strictly requires a `path`.

```python
import unittest
from pyramid import testing
from cornice.service import Service

class TestExistingRouteFeature(unittest.TestCase):
    def setUp(self):
        self.config = testing.setUp()
        self.config.include("cornice")
        # 1. User defines a route explicitly to use specific Pyramid features
        self.config.add_route('my_external_route', '/external')

    def test_define_service_with_existing_route(self):
        # 2. Attempt to attach a Service to 'my_external_route'
        # Currently, this raises a TypeError because 'path' is missing/required.
        try:
            svc = Service(name="my_service", pyramid_route="my_external_route")
            print("Service created successfully.")
        except TypeError as e:
            print(f"FAIL: {e}")
            raise

if __name__ == '__main__':
    unittest.main()
```

**Current Output:**
```
FAIL: __init__() missing 1 required positional argument: 'path'
```

## Expected Behavior
1. The provided test case should pass successfully.
2. Both `Service` definitions and the `@resource` decorator should support binding to an existing route name.
3. The implementation should ensure that a service is defined by *either* a new path *or* an existing route, but not both simultaneously.

The repository is at `/workspace/cornice`, checked out at commit `bd1c9d0a7853b8ea368c091a8d17eb2e2de2bdab`.