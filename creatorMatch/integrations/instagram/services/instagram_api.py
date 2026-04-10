from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone


class InstagramAPIError(Exception):
    pass


@dataclass
class InstagramTokenPayload:
    access_token: str
    token_type: str
    expires_in: int | None
    user_id: str

    @property
    def expires_at(self):
        if not self.expires_in:
            return None
        return timezone.now() + timedelta(seconds=self.expires_in)


class InstagramGraphClient:
    def __init__(self):
        self.app_id = settings.INSTAGRAM_APP_ID
        self.app_secret = settings.INSTAGRAM_APP_SECRET
        self.redirect_uri = settings.INSTAGRAM_REDIRECT_URI
        self.base_url = settings.INSTAGRAM_GRAPH_BASE_URL.rstrip("/")

    def _request(self, method: str, path: str, *, params=None) -> dict:
        url = f"{self.base_url}{path}"
        response = requests.request(method, url, params=params, timeout=15)
        try:
            payload = response.json()
        except ValueError as exc:
            raise InstagramAPIError("Instagram returned invalid JSON") from exc

        if response.status_code >= 400:
            error = payload.get("error", {})
            message = error.get("message") or payload
            raise InstagramAPIError(f"Instagram API error: {message}")
        return payload

    def exchange_code_for_short_token(self, code: str) -> InstagramTokenPayload:
        payload = self._request(
            "GET",
            "/oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
        )
        return InstagramTokenPayload(
            access_token=payload["access_token"],
            token_type=payload.get("token_type", "bearer"),
            expires_in=payload.get("expires_in"),
            user_id=str(payload["user_id"]),
        )

    def exchange_for_long_lived_token(self, short_token: str) -> dict:
        return self._request(
            "GET",
            "/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.app_secret,
                "access_token": short_token,
            },
        )

    def get_profile(self, access_token: str) -> dict:
        return self._request(
            "GET",
            "/me",
            params={
                "fields": "id,username,account_type,media_count",
                "access_token": access_token,
            },
        )

    def revoke_access(self, access_token: str) -> None:
        return None
