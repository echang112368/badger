import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone


REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_META_OAUTH_SCOPES = (
    "instagram_business_basic",
    "instagram_business_manage_messages",
    "instagram_business_manage_comments",
    "instagram_business_content_publish",
)


class MetaAPIError(Exception):
    """Raised when Meta Graph API returns an error response."""


def get_meta_api_base() -> str:
    return "https://graph.instagram.com"


def get_oauth_url_base() -> str:
    return "https://www.instagram.com/oauth/authorize"


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def build_oauth_url(state: str) -> str:
    scope_value = resolve_meta_oauth_scopes()
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.META_REDIRECT_URI,
        "scope": scope_value,
        "response_type": "code",
        "state": state,
        "enable_fb_login": str(getattr(settings, "META_ENABLE_FB_LOGIN", True)).lower(),
        "force_reauth": str(getattr(settings, "META_FORCE_REAUTH", False)).lower(),
    }
    return f"{get_oauth_url_base()}?{urlencode(params)}"


def resolve_meta_oauth_scopes() -> str:
    """Return OAuth scopes from settings as a stable comma-separated string."""

    raw_scopes = getattr(settings, "META_OAUTH_SCOPES", DEFAULT_META_OAUTH_SCOPES)
    if isinstance(raw_scopes, str):
        scope_list = [part.strip() for part in raw_scopes.split(",") if part.strip()]
    elif isinstance(raw_scopes, (list, tuple, set)):
        scope_list = [str(part).strip() for part in raw_scopes if str(part).strip()]
    else:
        scope_list = [scope for scope in DEFAULT_META_OAUTH_SCOPES]

    deduped: list[str] = []
    for scope in scope_list:
        if scope not in deduped:
            deduped.append(scope)

    if not deduped:
        deduped = [scope for scope in DEFAULT_META_OAUTH_SCOPES]

    return ",".join(deduped)


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise MetaAPIError("Meta API returned a non-JSON response.") from exc

    if not isinstance(payload, dict):
        raise MetaAPIError("Meta API returned an unexpected payload.")

    if response.status_code >= 400:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        message = error.get("message") or "Meta API request failed."
        raise MetaAPIError(message)

    if isinstance(payload.get("error"), dict):
        message = payload["error"].get("message") or "Meta API request failed."
        raise MetaAPIError(message)

    return payload


def exchange_code_for_access_token(code: str) -> dict[str, Any]:
    token_url = "https://api.instagram.com/oauth/access_token"
    response = requests.post(
        token_url,
        data={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": settings.META_REDIRECT_URI,
            "code": code,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    payload = _response_json(response)

    # Instagram Business Login may return either a flat payload or a data list.
    data_items = payload.get("data")
    if isinstance(data_items, list) and data_items and isinstance(data_items[0], dict):
        return data_items[0]
    return payload


def exchange_for_long_lived_access_token(short_lived_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_meta_api_base()}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.META_APP_SECRET,
            "access_token": short_lived_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def refresh_long_lived_access_token(access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_meta_api_base()}/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": access_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def should_refresh_token(token_expires_at, *, buffer_seconds: int = 24 * 60 * 60) -> bool:
    if not token_expires_at:
        return True
    return token_expires_at <= timezone.now() + timedelta(seconds=buffer_seconds)


def get_instagram_user(access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_meta_api_base()}/me",
        params={
            "fields": "id,user_id,username,followers_count,media_count",
            "access_token": access_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def token_expiry_from_response(token_data: dict[str, Any]):
    expires_in = token_data.get("expires_in")
    if not isinstance(expires_in, int) or expires_in <= 0:
        return None
    return timezone.now() + timedelta(seconds=expires_in)
