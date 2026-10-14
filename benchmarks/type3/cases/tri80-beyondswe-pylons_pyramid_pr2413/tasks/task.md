# Implement `require_csrf` view option to improve CSRF failure handling

## Description
We are looking to improve how CSRF validation failures are handled in the application. Currently, CSRF checks are often performed using the `check_csrf` view predicate. However, when a predicate check fails, Pyramid raises a `PredicateMismatch` exception, which results in a `404 Not Found` response.

This behavior is misleading for clients; a missing or invalid CSRF token should result in a `400 Bad Request` (or a specific `BadCSRFToken` exception), not a 404.

We need to introduce a new view configuration option, `require_csrf`, that supersedes the existing predicate approach. This option must ensure that validation occurs at a stage in the pipeline where a failure can raise the appropriate exception rather than causing a predicate mismatch.

Please review the architectural discussions and requirements in **[Issue #2367](https://github.com/Pylons/pyramid/issues/2367)** and the capabilities introduced in **[PR #2021](https://github.com/Pylons/pyramid/pull/2021)** to determine the best implementation strategy.

**Goals:**
1.  **Correct Status Codes:** Ensure that CSRF failures triggered by this new option result in a `400 Bad Request` response.
2.  **View Configuration:** The `require_csrf` option should be usable in `config.add_view`. Refer to the linked issue for the expected argument types (boolean, string, etc.) and behavior.
3.  **Global Defaults:** Implement a global configuration setting to enable this protection application-wide by default. The specific setting name and logic should align with the consensus in Issue #2367.
4.  **Method Safety:** The implementation must respect standard HTTP method semantics regarding which requests require CSRF protection (e.g., GET vs POST). Consult the linked issue for the security model details.

## Steps to Reproduce
The following script demonstrates the desired API usage. Currently, `require_csrf` is ignored, and the request succeeds (200 OK) without a token. The goal is for this script to result in a 400 Bad Request.

```python
from pyramid.config import Configurator
from pyramid.response import Response
from pyramid.request import Request
from pyramid.interfaces import ISessionFactory
from zope.interface import implementer

@implementer(ISessionFactory)
class DummySessionFactory:
    def __call__(self, request):
        return {'csrf_token': 'secret_token'}

def view(request):
    return Response('ok')

def main():
    config = Configurator()
    config.registry.registerUtility(DummySessionFactory(), ISessionFactory)
    
    # Feature Usage: We expect this to enforce CSRF checks and return 400 on failure
    config.add_view(view, name='protected', require_csrf=True)
    
    app = config.make_wsgi_app()
    
    # Simulate a POST request WITHOUT the CSRF token
    req = Request.blank('/protected', method='POST')
    
    # Currently returns 200 OK (Fail), should return 400 Bad Request (Success)
    resp = req.get_response(app)
    
    if resp.status_code == 200:
        print("FAIL: Request succeeded (200 OK) but should have been blocked.")
    elif resp.status_code == 400:
        print("SUCCESS: Request was blocked (400 Bad Request).")
    else:
        print(f"Unexpected status: {resp.status_code}")

if __name__ == '__main__':
    main()
```

## Expected Behavior
1.  The reproduction script should output `SUCCESS: Request was blocked (400 Bad Request).`
2.  The mechanism should allow enabling/disabling CSRF checks per-view and globally via configuration, as detailed in the external references.

The repository is at `/workspace/pyramid`, checked out at commit `b1527e793bc101327050370c17e1be698f7192ff`.