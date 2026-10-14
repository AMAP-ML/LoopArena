# Compatibility issues with Django 5.1 LoginRequiredMiddleware and DRF Views

## Description
We have upgraded our project to Django 5.1 and added the new `django.contrib.auth.middleware.LoginRequiredMiddleware` to the `MIDDLEWARE` setting to enforce global authentication.

However, this has caused regressions in our Django REST Framework (DRF) endpoints. Instead of handling authentication via DRF's internal `authentication_classes` and `permission_classes`, the API views are being intercepted by the global middleware.

## Symptoms
The following behaviors are observed in the test suite:
1.  **Public Views:** Endpoints configured with `AllowAny` permissions (which should be publicly accessible) are redirecting to the HTML login page (HTTP 302).
2.  **Protected Views:** Unauthenticated requests to API endpoints are returning HTTP 302 (redirect to login) instead of the expected HTTP 401 Unauthorized JSON response.

## Task
Investigate the interaction between the new Django 5.1 middleware and DRF views. Implement a fix that ensures:
*   The `LoginRequiredMiddleware` remains enabled globally for standard Django views.
*   DRF views (including `APIView` subclasses, `@api_view` function-based views, and `ViewSet`s) bypass the global login enforcement where appropriate, allowing DRF to handle permissions and return the correct HTTP status codes (200 or 401).

Ensure the solution covers both Class-Based Views and Function-Based Views.

The repository is at `/workspace/django-rest-framework`, checked out at commit `125ad42eb3922cd5ce1f4b332c91baf6d6162fb3`.