import logging
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone


logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_META_OAUTH_SCOPES = (
    "public_profile",
    "pages_show_list",
    "instagram_basic",
    "instagram_manage_insights",
    "pages_read_engagement",
)


class MetaAPIError(Exception):
    """Raised when Meta Graph API returns an error response."""


def get_graph_api_base() -> str:
    api_version = getattr(settings, "META_API_VERSION", "v22.0")
    return f"https://graph.facebook.com/{api_version}"


def get_oauth_url_base() -> str:
    return "https://www.facebook.com/dialog/oauth"


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
    }
    return f"{get_oauth_url_base()}?{urlencode(params)}"


def resolve_meta_oauth_scopes() -> str:
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
    print(
        "[instagram_oauth] exchanging code for access token",
        {
            "endpoint": f"{get_graph_api_base()}/oauth/access_token",
            "client_id": settings.META_APP_ID,
            "redirect_uri": settings.META_REDIRECT_URI,
            "code_preview": f"{code[:10]}..." if code else "",
        },
        flush=True,
    )
    response = requests.get(
        f"{get_graph_api_base()}/oauth/access_token",
        params={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "redirect_uri": settings.META_REDIRECT_URI,
            "code": code,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def exchange_for_long_lived_access_token(short_lived_token: str) -> dict[str, Any]:
    print(
        "[instagram_oauth] exchanging for long-lived access token",
        {
            "endpoint": f"{get_graph_api_base()}/oauth/access_token",
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "short_lived_token_preview": (
                f"{short_lived_token[:10]}..." if short_lived_token else ""
            ),
        },
        flush=True,
    )
    response = requests.get(
        f"{get_graph_api_base()}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": short_lived_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def should_refresh_token(token_expires_at, *, buffer_seconds: int = 24 * 60 * 60) -> bool:
    if not token_expires_at:
        return True
    return token_expires_at <= timezone.now() + timedelta(seconds=buffer_seconds)


def get_facebook_user(access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_graph_api_base()}/me",
        params={"fields": "id,name", "access_token": access_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def get_user_pages(access_token: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{get_graph_api_base()}/me/accounts",
        params={"fields": "id,name,access_token", "access_token": access_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    payload = _response_json(response)
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def get_page_instagram_business_account(page_id: str, page_access_token: str) -> dict[str, Any] | None:
    response = requests.get(
        f"{get_graph_api_base()}/{page_id}",
        params={
            "fields": "instagram_business_account{id,username,followers_count,media_count}",
            "access_token": page_access_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    payload = _response_json(response)
    ig_account = payload.get("instagram_business_account")
    if isinstance(ig_account, dict):
        return ig_account
    return None


def get_instagram_user(ig_user_id: str, access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_graph_api_base()}/{ig_user_id}",
        params={
            "fields": "id,username,followers_count,media_count,account_type",
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
