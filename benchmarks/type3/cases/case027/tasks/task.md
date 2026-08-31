# Implement `RabbitMQAPI` wrapper for RabbitMQ Management HTTP API

## Description
We require a new utility class, `RabbitMQAPI`, to be implemented in `zocalo.util.rabbitmq`. This class will serve as a structured Python wrapper around the [RabbitMQ Management HTTP API](https://rawcdn.githack.com/rabbitmq/rabbitmq-server/v3.9.7/deps/rabbitmq_management/priv/www/api/index.html).

Currently, there is no unified programmatic interface for monitoring metrics, health checks, and performing configuration tasks (such as declaring/deleting queues and exchanges) within the codebase.

**Requirements:**
1.  **Dependencies:** The implementation must use `requests` for HTTP operations and `pydantic` for data modeling.
2.  **Configuration:** The class must be initializable via a `zocalo.configuration.Configuration` object, specifically reading settings from the `rabbitmqapi` key.
3.  **Functionality:** The wrapper must support the operations defined in the provided reproduction script, covering:
    *   System overview and health.
    *   Monitoring of nodes and connections.
    *   Management (List/Create/Delete) of Exchanges, Queues, and Users.
    *   Management (List/Set/Clear) of Policies.
4.  **Data Models:** API responses must be parsed into appropriate Pydantic models representing the resources, rather than returning raw dictionaries.

## Steps to Reproduce / Interface Contract
The following script defines the expected interface and behavior. It attempts to import and use the `RabbitMQAPI` class. Currently, this script fails because the class and its methods do not exist.

```python
import sys
from unittest import mock
import zocalo.configuration

# Attempt to import the new class
try:
    from zocalo.util.rabbitmq import RabbitMQAPI
except ImportError:
    print("FAIL: Could not import RabbitMQAPI from zocalo.util.rabbitmq")
    sys.exit(1)

# Mock configuration
zc = mock.MagicMock(zocalo.configuration.Configuration)
zc.rabbitmqapi = {
    "base_url": "http://localhost:15672/api",
    "username": "guest",
    "password": "guest",
}

# Test Initialization
try:
    rmq = RabbitMQAPI.from_zocalo_configuration(zc)
    print("SUCCESS: RabbitMQAPI initialized")
except AttributeError:
    print("FAIL: from_zocalo_configuration method missing")
    sys.exit(1)

# Test Method Existence
required_methods = [
    "overview", # or health_checks
    "connections",
    "nodes",
    "exchanges", "exchange_declare", "exchange_delete",
    "queues", "queue_declare", "queue_delete",
    "policies", "set_policy", "clear_policy",
    "users", "add_user", "delete_user"
]

missing = []
for method in required_methods:
    if not hasattr(rmq, method):
        missing.append(method)

if missing:
    print(f"FAIL: Missing methods: {missing}")
    sys.exit(1)

print("SUCCESS: All required methods defined")
```

## Expected Behavior
1.  The reproduction script must execute successfully and print `SUCCESS: All required methods defined`.
2.  The implemented methods must correctly interact with the endpoints described in the [RabbitMQ API documentation](https://rawcdn.githack.com/rabbitmq/rabbitmq-server/v3.9.7/deps/rabbitmq_management/priv/www/api/index.html).
3.  Methods must return valid Pydantic model instances populated with the API response data.