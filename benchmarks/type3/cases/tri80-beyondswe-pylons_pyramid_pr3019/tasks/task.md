# Implement decoupled `ICSRFStoragePolicy` and verify `CookieCSRFStoragePolicy` behavior

## Description
We are finalizing the effort to decouple CSRF protection from the session machinery, allowing for pluggable storage backends (such as cookies). This work builds upon the architecture originally proposed in [PR #2854](https://github.com/Pylons/pyramid/pull/2854).

Please implement the `ICSRFStoragePolicy` interface and the associated default policies (`LegacySessionCSRFStoragePolicy`, `SessionCSRFStoragePolicy`, and `CookieCSRFStoragePolicy`) following the design discussions in the linked PR.

The implementation of `CookieCSRFStoragePolicy` must be robust and satisfy specific requirements regarding token lifecycle and configuration. A test suite has been provided below to verify the expected behavior.

## Steps to Reproduce
The following test case defines the expected behavior for the `CookieCSRFStoragePolicy`. Ensure your implementation passes these tests.

```python
import unittest
from pyramid import testing
# Note: implementation of this class is the goal of this task
from pyramid.csrf import CookieCSRFStoragePolicy

class TestCookieCSRFStoragePolicy(unittest.TestCase):
    def test_get_csrf_token_returns_new_token_immediately(self):
        # Scenario: A token exists in cookies, but we force generation of a new one.
        request = testing.DummyRequest()
        request.cookies = {'csrf_token': 'old_token'}

        policy = CookieCSRFStoragePolicy()

        # Verify initial state: returns token from cookie
        self.assertEqual(policy.get_csrf_token(request), 'old_token')

        # Generate new token
        new_token = policy.new_csrf_token(request)
        self.assertNotEqual(new_token, 'old_token')

        # Verify that the policy returns the new_token that was just generated
        self.assertEqual(policy.get_csrf_token(request), new_token)

    def test_domain_setting_applied(self):
        # Verify domain is passed to the underlying cookie generation
        request = testing.DummyRequest()
        policy = CookieCSRFStoragePolicy(domain='example.com')

        policy.new_csrf_token(request)

        response = testing.DummyResponse()
        # Execute callbacks to populate response headers
        for callback in request.response_callbacks:
            callback(request, response)

        # Check if domain is in the Set-Cookie header
        cookie_headers = [v for k, v in response.headerlist if k == 'Set-Cookie']
        self.assertTrue(any('Domain=example.com' in h for h in cookie_headers),
                        "Domain 'example.com' not found in Set-Cookie headers")

if __name__ == '__main__':
    unittest.main()
```

## Expected Behavior
*   **Architecture:** The codebase should support `ICSRFStoragePolicy` with the distinct implementations (Legacy vs Session vs Cookie) as outlined in the context of [PR #2854](https://github.com/Pylons/pyramid/pull/2854).
*   **Verification:** The `CookieCSRFStoragePolicy` must pass the provided `TestCookieCSRFStoragePolicy` suite, ensuring correct token retrieval and cookie configuration.

The repository is at `/workspace/pyramid`, checked out at commit `87af11c5e33b8c03d57a8b571f0b152efe866af1`.
