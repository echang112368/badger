"""Minimal views that emulate ``rest_framework_simplejwt`` endpoints."""

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import TokenError
from .tokens import RefreshToken


class TokenObtainPairView(APIView):
    """Authenticate a user and issue access/refresh tokens."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        data = request.data or {}
        identifier = data.get("username") or data.get("email")
        password = data.get("password")

        if not identifier or not password:
            return Response(
                {"detail": "Username/email and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        User = get_user_model()
        user = None

        lookup_fields = {User.USERNAME_FIELD}
        if hasattr(User, "email"):
            lookup_fields.add("email")

        for field in lookup_fields:
            try:
                candidate = User.objects.get(Q(**{field: identifier}))
            except User.DoesNotExist:
                continue
            if candidate.check_password(password):
                user = candidate
                break

        if user is None or not user.is_active:
            return Response(
                {"detail": "No active account found with the given credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        refresh = RefreshToken.for_user(user)
        return Response({"refresh": str(refresh), "access": str(refresh.access_token)})


class TokenRefreshView(APIView):
    """Return a fresh access token for a provided refresh token."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        refresh_token = request.data.get("refresh") if request.data else None
        if not refresh_token:
            return Response(
                {"detail": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refresh = RefreshToken(refresh_token)
        except TokenError:
            return Response(
                {"detail": "Refresh token is invalid or has expired."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response({"refresh": str(refresh), "access": str(refresh.access_token)})
