"""
Custom DRF authentication backend for API key authentication.

This backend checks for the `key` parameter in request.POST (form-encoded body)
or request.GET (query string), then looks up the corresponding User.

It co-exists with JWTAuthentication — both are listed in DEFAULT_AUTHENTICATION_CLASSES.
DRF tries each backend in order and uses the first one that returns a user.
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth import get_user_model

User = get_user_model()


class APIKeyAuthentication(BaseAuthentication):
    """
    Authenticates requests using a plain API key passed as `key=` in POST body or query string.

    Used by the public SMM Panel API v2 endpoint (/api/v2/).
    Regular JWT-authenticated endpoints are unaffected.
    """

    def authenticate(self, request):
        api_key = None

        # 1. Check query parameters (?key=...)
        if hasattr(request, 'query_params') and request.query_params.get('key'):
            api_key = request.query_params.get('key')
        elif hasattr(request, 'GET') and request.GET.get('key'):
            api_key = request.GET.get('key')

        # 2. Check body (Form-urlencoded or JSON payload)
        if not api_key:
            if hasattr(request, 'data') and isinstance(request.data, dict) and request.data.get('key'):
                api_key = request.data.get('key')
            elif hasattr(request, 'POST') and request.POST.get('key'):
                api_key = request.POST.get('key')

        if not api_key:
            # No key found — let other authenticators (JWT) have a go
            return None

        try:
            user = User.objects.get(api_key=api_key, is_active=True)
        except User.DoesNotExist:
            raise AuthenticationFailed('Invalid API key.')

        return (user, None)

    def authenticate_header(self, request):
        """
        Return a string that will be used as the value of the WWW-Authenticate
        header in a HTTP 401 Unauthenticated response.
        """
        return 'APIKey realm="api"'
