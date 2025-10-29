"""Minimal token helpers that emulate a subset of SimpleJWT behaviour."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, Optional

from django.conf import settings
from django.core import signing
from django.utils import timezone

from .exceptions import TokenError


_DEFAULT_ACCESS_LIFETIME = timedelta(minutes=5)
_DEFAULT_REFRESH_LIFETIME = timedelta(days=7)
_ACCESS_SALT = "rest_framework_simplejwt.access"
_REFRESH_SALT = "rest_framework_simplejwt.refresh"


def _get_lifetime(setting_key: str, default: timedelta) -> timedelta:
    """Fetch a lifetime from ``settings.SIMPLE_JWT`` with a default."""

    simple_jwt_settings = getattr(settings, "SIMPLE_JWT", {})
    value = simple_jwt_settings.get(setting_key)
    if isinstance(value, timedelta):
        return value
    return default


def _seconds(value: timedelta) -> int:
    """Return the integer number of seconds contained in ``value``."""

    return max(int(value.total_seconds()), 0)


@dataclass
class BaseToken:
    """Shared implementation for the concrete token classes."""

    payload: Dict[str, Any]
    salt: str
    lifetime: timedelta
    token: Optional[str] = None

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        payload: Optional[Dict[str, Any]] = None,
        salt: str,
        lifetime: timedelta,
    ) -> None:
        self.salt = salt
        self.lifetime = lifetime
        if token is None:
            self.payload = dict(payload or {})
            self.token = None
        else:
            try:
                self.payload = signing.loads(
                    token,
                    key=settings.SECRET_KEY,
                    salt=self.salt,
                    max_age=_seconds(self.lifetime),
                )
            except signing.BadSignature as exc:
                raise TokenError() from exc
            if not isinstance(self.payload, dict):
                raise TokenError()
            self.token = token

    def __str__(self) -> str:
        if self.token is None:
            self.token = signing.dumps(
                self.payload,
                key=settings.SECRET_KEY,
                salt=self.salt,
            )
        return self.token

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    def _stamp(self, token_type: str) -> None:
        """Ensure the payload contains standard metadata fields."""

        now = timezone.now()
        issued_at = int(now.timestamp())
        expires_at = int((now + self.lifetime).timestamp())
        self.payload.setdefault("token_type", token_type)
        self.payload.setdefault("iat", issued_at)
        self.payload.setdefault("exp", expires_at)


class AccessToken(BaseToken):
    """A lightweight signed access token."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        lifetime = _get_lifetime("ACCESS_TOKEN_LIFETIME", _DEFAULT_ACCESS_LIFETIME)
        super().__init__(token, payload=payload, salt=_ACCESS_SALT, lifetime=lifetime)
        if token is None:
            if "user_id" not in self.payload:
                raise ValueError("AccessToken requires a user_id in the payload.")
            self._stamp("access")
        else:
            if self.payload.get("token_type") != "access":
                raise TokenError()


class RefreshToken(BaseToken):
    """A lightweight signed refresh token."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        lifetime = _get_lifetime("REFRESH_TOKEN_LIFETIME", _DEFAULT_REFRESH_LIFETIME)
        super().__init__(token, payload=payload, salt=_REFRESH_SALT, lifetime=lifetime)
        if token is None:
            if "user_id" not in self.payload:
                raise ValueError("RefreshToken requires a user_id in the payload.")
            self._stamp("refresh")
        else:
            if self.payload.get("token_type") != "refresh":
                raise TokenError()

    @classmethod
    def for_user(cls, user) -> "RefreshToken":
        """Create a refresh token for ``user``."""

        return cls(payload={"user_id": user.pk})

    @property
    def access_token(self) -> AccessToken:
        """Return a matching access token for the same user."""

        return AccessToken(payload={"user_id": self.get("user_id")})
