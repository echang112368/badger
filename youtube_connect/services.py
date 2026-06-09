import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone


REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_YOUTUBE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
)


class YouTubeAPIError(Exception):
    """Raised when YouTube API returns an error response."""


def get_youtube_api_base() -> str:
    return "https://www.googleapis.com/youtube/v3"


def get_youtube_analytics_api_base() -> str:
    return "https://youtubeanalytics.googleapis.com/v2"


def get_oauth_url_base() -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth"


def get_oauth_token_url() -> str:
    return "https://oauth2.googleapis.com/token"


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def resolve_youtube_oauth_scopes() -> str:
    raw_scopes = getattr(settings, "YOUTUBE_OAUTH_SCOPES", DEFAULT_YOUTUBE_OAUTH_SCOPES)
    if isinstance(raw_scopes, str):
        scope_list = [part.strip() for part in raw_scopes.replace(",", " ").split() if part.strip()]
    elif isinstance(raw_scopes, (list, tuple, set)):
        scope_list = [str(part).strip() for part in raw_scopes if str(part).strip()]
    else:
        scope_list = [scope for scope in DEFAULT_YOUTUBE_OAUTH_SCOPES]

    deduped: list[str] = []
    for scope in scope_list:
        if scope not in deduped:
            deduped.append(scope)

    if not deduped:
        deduped = [scope for scope in DEFAULT_YOUTUBE_OAUTH_SCOPES]

    return " ".join(deduped)


def build_oauth_url(state: str, force_account_picker: bool = False) -> str:
    params = {
        "client_id": settings.YOUTUBE_CLIENT_ID,
        "redirect_uri": settings.YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "state": state,
        "scope": resolve_youtube_oauth_scopes(),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account consent" if force_account_picker else "consent",
    }
    return f"{get_oauth_url_base()}?{urlencode(params)}"


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise YouTubeAPIError("YouTube API returned a non-JSON response.") from exc

    if not isinstance(payload, dict):
        raise YouTubeAPIError("YouTube API returned an unexpected payload.")

    error = payload.get("error")
    if response.status_code >= 400 or error:
        if isinstance(error, dict):
            message = error.get("message") or error.get("description")
        else:
            message = payload.get("error_description") or str(error or "")
        raise YouTubeAPIError(message or "YouTube API request failed.")

    return payload


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    response = requests.post(
        get_oauth_token_url(),
        data={
            "client_id": settings.YOUTUBE_CLIENT_ID,
            "client_secret": settings.YOUTUBE_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": settings.YOUTUBE_REDIRECT_URI,
            "code": code,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    response = requests.post(
        get_oauth_token_url(),
        data={
            "client_id": settings.YOUTUBE_CLIENT_ID,
            "client_secret": settings.YOUTUBE_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def should_refresh_token(token_expires_at, *, buffer_seconds: int = 10 * 60) -> bool:
    if not token_expires_at:
        return True
    return token_expires_at <= timezone.now() + timedelta(seconds=buffer_seconds)


def token_expiry_from_response(token_data: dict[str, Any]):
    expires_in = token_data.get("expires_in")
    if not isinstance(expires_in, int) or expires_in <= 0:
        return None
    return timezone.now() + timedelta(seconds=expires_in)


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def get_authenticated_channel(access_token: str) -> dict[str, Any]:
    response = requests.get(
        f"{get_youtube_api_base()}/channels",
        params={"part": "snippet,contentDetails,statistics,brandingSettings", "mine": "true"},
        headers=_auth_headers(access_token),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    payload = _response_json(response)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        raise YouTubeAPIError("No YouTube channel was returned for this account.")
    return items[0]


def fetch_uploads_playlist_items(access_token: str, uploads_playlist_id: str, limit: int = 20) -> dict[str, Any]:
    response = requests.get(
        f"{get_youtube_api_base()}/playlistItems",
        params={
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": min(max(int(limit or 20), 1), 50),
        },
        headers=_auth_headers(access_token),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def fetch_videos(access_token: str, video_ids: list[str]) -> dict[str, Any]:
    ids = [str(video_id).strip() for video_id in video_ids if str(video_id).strip()]
    if not ids:
        return {"items": []}
    response = requests.get(
        f"{get_youtube_api_base()}/videos",
        params={"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids)},
        headers=_auth_headers(access_token),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)


def query_youtube_analytics(
    access_token: str,
    *,
    channel_id: str,
    start_date: str,
    end_date: str,
    metrics: str,
    dimensions: str | None = None,
    filters: str | None = None,
    sort: str | None = None,
    max_results: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "ids": f"channel=={channel_id}",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
    }
    if dimensions:
        params["dimensions"] = dimensions
    if filters:
        params["filters"] = filters
    if sort:
        params["sort"] = sort
    if max_results:
        params["maxResults"] = max_results

    response = requests.get(
        f"{get_youtube_analytics_api_base()}/reports",
        params=params,
        headers=_auth_headers(access_token),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return _response_json(response)
