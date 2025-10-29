"""Minimal JWT authentication backend used by the project tests.""" 

from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from .exceptions import TokenError
from .tokens import AccessToken


class JWTAuthentication(BaseAuthentication):
    """Authenticate requests using the custom ``AccessToken`` implementation."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header:
            return None

        if header[0].decode().lower() != self.keyword.lower():
            return None

        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid Authorization header.")

        try:
            raw_token = header[1].decode()
        except UnicodeError as exc:
            raise exceptions.AuthenticationFailed("Invalid characters in token.") from exc

        try:
            token = AccessToken(raw_token)
        except TokenError as exc:
            raise exceptions.AuthenticationFailed(str(exc)) from exc

        user_id = token.get("user_id")
        if not user_id:
            raise exceptions.AuthenticationFailed("Token missing user information.")

        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed("User not found.") from exc

        return (user, token)
