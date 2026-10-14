# [Security: CSRF Protection Gaps regarding Unsafe Methods and Token Sources]

## Description
A security review has identified deficiencies in our current CSRF protection implementation. The current logic does not fully align with **RFC 2616** best practices regarding "unsafe" methods and exposes tokens to potential leakage vectors.

**Issues Identified:**
1.  **Method Coverage:** The system currently enforces CSRF checks on `POST` requests but fails to validate other state-changing methods.
2.  **Token Leakage:** The mechanism for retrieving CSRF tokens is overly permissive. It currently accepts tokens from sources that are logged or transmitted in the `Referer` header (e.g., query string parameters), which poses a security risk.

**Task:**
Update the CSRF protection logic to:
1.  Enforce validation for **all** HTTP methods classified as "unsafe" by [RFC 2616 Section 9](https://www.w3.org/Protocols/rfc2616/rfc2616-sec9.html).
2.  Restrict token acceptance to secure sources only (e.g., headers or the request body), explicitly excluding sources susceptible to URL leakage.

**Dependency Investigation (WebOb):**
This update may require accessing request body data on methods other than `POST`. Historically, there were concerns regarding the underlying **WebOb** library's behavior when accessing body attributes on non-POST requests (specifically regarding buffering or side effects).
You must investigate the current **WebOb** behavior to determine the safe and correct way to access body parameters for these additional methods without introducing performance regressions or unintended side effects.

## Steps to Reproduce
The following test case demonstrates the vulnerability where a `PUT` request bypasses the CSRF check despite being an unsafe method.

```python
import unittest
from pyramid import testing
from pyramid.exceptions import BadCSRFToken
from pyramid.interfaces import IViewClassifier
from zope.interface import providedBy

class TestCSRFGap(unittest.TestCase):
    def setUp(self):
        self.config = testing.setUp()
        self.config.include('pyramid.viewderivers')

    def tearDown(self):
        testing.tearDown()

    def test_put_request_bypasses_csrf(self):
        # 1. Define a view that requires CSRF
        self.config.add_view(
            lambda c, r: "View Executed", 
            name='unsafe_view', 
            require_csrf=True
        )

        # 2. Create a PUT request (Unsafe method)
        request = testing.DummyRequest()
        request.method = 'PUT'
        
        # 3. Setup Session with a valid token
        request.session = {'csrf_token': 'valid_token'}
        class DummySession(dict):
            def get_csrf_token(self): return 'valid_token'
        request.session = DummySession({'csrf_token': 'valid_token'})
        
        # 4. Provide a WRONG token in headers
        # This SHOULD fail if checks were active for PUT
        request.headers['X-CSRF-Token'] = 'invalid_token'
        
        # Ensure request.POST is present (simulating body)
        request.POST = {}

        # 5. Execute View
        view = self.config.registry.adapters.lookup(
            (IViewClassifier, providedBy(request), providedBy(None)),
            IViewClassifier, name='unsafe_view')

        try:
            result = view(None, request)
            # FAILURE: The view executed despite the invalid token
            print(f"FAIL: View executed with result: '{result}' (Should have raised BadCSRFToken)")
        except BadCSRFToken:
            print("PASS: BadCSRFToken raised correctly.")

if __name__ == '__main__':
    unittest.main()
```

The repository is at `/workspace/pyramid`, checked out at commit `bf33b200bbb72114ca55150724b0a4c51d7ef535`.