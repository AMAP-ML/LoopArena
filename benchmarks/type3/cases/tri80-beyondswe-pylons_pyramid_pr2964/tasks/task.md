# Support custom execution policies for request retry logic

## Description
I am currently working on updates for `pyramid_tm` (the transaction manager extension) to support "retryable" requests—for example, automatically retrying a request that fails due to a transient database conflict. To implement this effectively, we need the ability to completely discard the state of a failed request and create a fresh request object from the original WSGI `environ`.

However, the current architecture makes it difficult for an external tool to control the request lifecycle at the creation stage. Tweens are insufficient because they wrap a request that has already been instantiated.

We need to introduce a hook in Pyramid that allows a developer to define a callable to wrap the execution logic. This callable will be responsible for creating the request object and invoking the pipeline, providing full control to retry, swap, or modify the process.

Please implement the necessary changes to support the API usage demonstrated in the reproduction script below.

## Steps to Reproduce / Logs
The following script demonstrates the API we wish to use. Currently, it fails with an `AttributeError` because the feature is not implemented.

```python
import unittest
from pyramid.config import Configurator
from pyramid.response import Response

class TestExecutionPolicyFeature(unittest.TestCase):
    def test_custom_execution_policy(self):
        # Define a custom policy
        # It accepts the WSGI environ and the router instance
        def custom_policy(environ, router):
            # The policy checks if the router exposes necessary methods
            # to manually create and invoke requests, which are needed for
            # implementing retry logic.
            if not hasattr(router, 'make_request') or not hasattr(router, 'invoke_request'):
                return Response(body=b'Router missing methods')

            # In a real scenario, we might catch an exception here and retry.
            # For this test, we simply return a response proving we intercepted control.
            return Response(body=b'executed via policy')

        config = Configurator()

        # This method is currently missing in the codebase
        if not hasattr(config, 'set_execution_policy'):
            raise AttributeError("Configurator.set_execution_policy not found.")

        config.set_execution_policy(custom_policy)
        app = config.make_wsgi_app()

        # Mock WSGI environ
        environ = {
            'wsgi.url_scheme': 'http',
            'PATH_INFO': '/',
            'REQUEST_METHOD': 'GET',
            'SERVER_NAME': 'localhost',
            'SERVER_PORT': '80',
        }
        start_response = lambda status, headers: None

        # Invoke the app
        response_iter = app(environ, start_response)
        body = b''.join(response_iter)

        # Expectation: The custom policy intercepts the call
        self.assertEqual(body, b'executed via policy')

if __name__ == '__main__':
    unittest.main()
```

## Expected Behavior
The provided test script should pass successfully. The application should respect the registered execution policy, and the router should expose the necessary methods to allow the policy to manage request creation and invocation.