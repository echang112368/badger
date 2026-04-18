import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone


REQUEST_TIMEOUT_SECONDS = 15


class MetaAPIError(Exception):
    """Raised when Meta Graph API returns an error response."""


def get_meta_api_base() -> str:
    return f"https://graph.facebook.com/{settings.META_API_VERSION}"


def get_oauth_url_base() -> str:
    return f"https://www.facebook.com/{settings.META_API_VERSION}/dialog/oauth"


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def build_oauth_url(state: str) -> str:
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.META_REDIRECT_URI,
        "scope": "instagram_basic,pages_show_list",
        "response_type": "code",
        "state": state,
    }
    return f"{get_oauth_url_base()}?{urlencode(params)}"


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
    token_url = f"{get_meta_api_base()}/oauth/access_token"
    response = requests.get(
        token_url,
        params={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "redirect_uri": settings.META_REDIRECT_URI,
            "code": code,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def get_user_profile(access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_meta_api_base()}/me",
        params={"fields": "id,name", "access_token": access_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def get_user_pages(access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_meta_api_base()}/me/accounts",
        params={
            "fields": "id,name,instagram_business_account",
            "access_token": access_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def choose_page_with_instagram(pages_payload: dict[str, Any]) -> dict[str, Any] | None:
    pages = pages_payload.get("data")
    if not isinstance(pages, list):
        return None

    for page in pages:
        if not isinstance(page, dict):
            continue
        ig_account = page.get("instagram_business_account")
        if isinstance(ig_account, dict) and ig_account.get("id"):
            return page
    return None


def get_instagram_user(ig_user_id: str, access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_meta_api_base()}/{ig_user_id}",
        params={
            "fields": "id,username,followers_count,media_count",
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
