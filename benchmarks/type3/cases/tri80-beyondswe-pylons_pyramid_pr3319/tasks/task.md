# Support `samesite` option in `AuthTktAuthenticationPolicy` and `CookieCSRFStoragePolicy`

## Description
We need to enable configuration of the `SameSite` cookie attribute within Pyramid's authentication and CSRF protection policies. Currently, the API does not appear to support this configuration, preventing users from enforcing stricter cookie policies.

Context regarding this feature request can be found in **[Issue #2733](https://github.com/Pylons/pyramid/issues/2733)**.

## Steps to Reproduce
The following script demonstrates the desired usage of the `samesite` argument. Currently, running this script results in a `TypeError`.

```python
import unittest
from pyramid.authentication import AuthTktAuthenticationPolicy
from pyramid.csrf import CookieCSRFStoragePolicy

class TestSameSiteSupport(unittest.TestCase):
    def test_auth_policy_samesite_arg(self):
        print("Testing AuthTktAuthenticationPolicy with samesite='Lax'...")
        # This currently raises TypeError: __init__() got an unexpected keyword argument 'samesite'
        policy = AuthTktAuthenticationPolicy('secret', samesite='Lax')

    def test_csrf_policy_samesite_arg(self):
        print("Testing CookieCSRFStoragePolicy with samesite='Strict'...")
        # This currently raises TypeError: __init__() got an unexpected keyword argument 'samesite'
        policy = CookieCSRFStoragePolicy(samesite='Strict')

if __name__ == '__main__':
    unittest.main()
```

## Expected Behavior
The `AuthTktAuthenticationPolicy` and `CookieCSRFStoragePolicy` classes should accept the `samesite` argument as shown in the reproduction script.

When the application generates responses, the `Set-Cookie` headers should include the `SameSite` attribute matching the provided configuration (e.g., `SameSite=Lax` or `SameSite=Strict`).

The repository is at `/workspace/pyramid`, checked out at commit `0760eba8fd5a0d8f0424c329ce92e9fb8d003f11`.