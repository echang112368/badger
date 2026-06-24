import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from creators.models import SocialAnalyticsSnapshot
from creators.services.social_dashboard import YouTubeAnalyticsService

from .models import YouTubeConnection
from .services import (
    YouTubeAPIError,
    build_oauth_url,
    exchange_code_for_tokens,
    generate_oauth_state,
    get_authenticated_channel,
    refresh_access_token,
    should_refresh_token,
    token_expiry_from_response,
)

logger = logging.getLogger(__name__)


def _channel_defaults(channel: dict, token_data: dict, access_token: str, refresh_token: str | None = None) -> dict:
    snippet = channel.get("snippet") if isinstance(channel.get("snippet"), dict) else {}
    statistics = channel.get("statistics") if isinstance(channel.get("statistics"), dict) else {}
    return {
        "platform": YouTubeConnection.PLATFORM_YOUTUBE,
        "youtube_channel_id": str(channel.get("id") or ""),
        "youtube_channel_title": snippet.get("title") or "",
        "youtube_channel_handle": snippet.get("customUrl") or snippet.get("handle") or "",
        "youtube_custom_url": snippet.get("customUrl") or "",
        "subscribers_count": int(statistics.get("subscriberCount") or 0),
        "video_count": int(statistics.get("videoCount") or 0),
        "view_count": int(statistics.get("viewCount") or 0),
        "youtube_access_token": access_token,
        "token_expires_at": token_expiry_from_response(token_data),
        "last_synced_at": timezone.now(),
        "raw_profile_data": channel,
        "raw_channel_statistics": statistics,
        **({"youtube_refresh_token": refresh_token} if refresh_token is not None else {}),
    }


def _ensure_fresh_access_token(connection: YouTubeConnection, *, force_refresh: bool = False) -> None:
    if not force_refresh and not should_refresh_token(connection.token_expires_at):
        return
    if not connection.youtube_refresh_token:
        raise YouTubeAPIError("YouTube refresh token is missing. Please reconnect YouTube.")

    refreshed_token_data = refresh_access_token(connection.youtube_refresh_token)
    refreshed_access_token = refreshed_token_data.get("access_token")
    if not refreshed_access_token:
        raise YouTubeAPIError("YouTube did not return a refreshed access token.")

    connection.youtube_access_token = refreshed_access_token
    connection.token_expires_at = token_expiry_from_response(refreshed_token_data)
    update_fields = ["youtube_access_token", "token_expires_at"]
    if refreshed_token_data.get("refresh_token"):
        connection.youtube_refresh_token = refreshed_token_data["refresh_token"]
        update_fields.append("youtube_refresh_token")
    connection.save(update_fields=update_fields)


def _normalise_sync_error_message(error: YouTubeAPIError) -> str:
    message = str(error).lower()
    if "invalid" in message or "expired" in message or "revoked" in message:
        return "YouTube access token has expired or been revoked. Please reconnect YouTube."
    return str(error).strip() or "Unable to sync YouTube metrics right now. Please try again."


@login_required
@require_GET
def connect_youtube(request):
    state = generate_oauth_state()
    request.session["youtube_oauth_state"] = state
    already_connected = YouTubeConnection.objects.filter(user=request.user).exists()
    return redirect(build_oauth_url(state, force_account_picker=already_connected))


@login_required
@require_GET
def youtube_callback(request):
    settings_url = reverse("creator_settings")

    if request.GET.get("error"):
        return redirect(f"{settings_url}?youtube_oauth=error")

    code = request.GET.get("code")
    if not code:
        return redirect(f"{settings_url}?youtube_oauth=error")

    expected_state = request.session.get("youtube_oauth_state")
    received_state = request.GET.get("state")
    if not expected_state or expected_state != received_state:
        return redirect(f"{settings_url}?youtube_oauth=error")

    try:
        token_data = exchange_code_for_tokens(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise YouTubeAPIError("YouTube did not return an access token.")

        channel = get_authenticated_channel(access_token)
        youtube_channel_id = str(channel.get("id") or "")
        if not youtube_channel_id:
            raise YouTubeAPIError("YouTube channel response is missing an id.")

        now = timezone.now()
        defaults = _channel_defaults(
            channel,
            token_data,
            access_token,
            refresh_token=token_data.get("refresh_token"),
        )
        connection, created = YouTubeConnection.objects.update_or_create(
            user=request.user,
            defaults=defaults,
        )
        if created:
            connection.connected_at = now
            connection.save(update_fields=["connected_at"])

    except YouTubeAPIError as exc:
        logger.warning("YouTube OAuth callback failed: %s", exc)
        return redirect(f"{settings_url}?youtube_oauth=error")
    finally:
        request.session.pop("youtube_oauth_state", None)

    return redirect(f"{settings_url}?youtube_oauth=success")


@login_required
@require_POST
def youtube_disconnect(request):
    SocialAnalyticsSnapshot.objects.filter(
        user=request.user,
        platform=SocialAnalyticsSnapshot.PLATFORM_YOUTUBE,
    ).delete()
    YouTubeConnection.objects.filter(user=request.user).delete()
    request.session.pop("youtube_oauth_state", None)
    return redirect(f"{reverse('creator_settings')}?youtube_disconnect=success")


@login_required
@require_GET
def youtube_status(request):
    try:
        connection = request.user.youtube_connection
    except YouTubeConnection.DoesNotExist:
        return JsonResponse({"connected": False})

    try:
        _ensure_fresh_access_token(connection)
    except YouTubeAPIError as exc:
        logger.warning("Unable to refresh YouTube token for status endpoint: %s", exc)

    return JsonResponse(
        {
            "connected": True,
            "youtube_channel_id": connection.youtube_channel_id,
            "youtube_channel_title": connection.youtube_channel_title,
            "youtube_channel_handle": connection.youtube_channel_handle,
            "youtube_custom_url": connection.youtube_custom_url,
            "subscribers_count": connection.subscribers_count,
            "video_count": connection.video_count,
            "view_count": connection.view_count,
            "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
            "connected_at": connection.connected_at.isoformat() if connection.connected_at else None,
            "token_expires_at": connection.token_expires_at.isoformat() if connection.token_expires_at else None,
        }
    )


@login_required
@require_GET
def youtube_sync(request):
    try:
        connection = request.user.youtube_connection
    except YouTubeConnection.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "not_connected",
                "message": "No YouTube connection found for this account.",
            },
            status=404,
        )

    try:
        _ensure_fresh_access_token(connection, force_refresh=True)
        channel = get_authenticated_channel(connection.youtube_access_token)
        snapshot_payload = YouTubeAnalyticsService(request.user).fetch_and_cache(connection)
    except YouTubeAPIError as exc:
        return JsonResponse(
            {
                "success": False,
                "error": "youtube_api_error",
                "message": _normalise_sync_error_message(exc),
            },
            status=400,
        )

    account = snapshot_payload.get("account", {}) if isinstance(snapshot_payload, dict) else {}
    statistics = channel.get("statistics") if isinstance(channel.get("statistics"), dict) else {}
    snippet = channel.get("snippet") if isinstance(channel.get("snippet"), dict) else {}
    connection.youtube_channel_title = account.get("title") or snippet.get("title") or connection.youtube_channel_title
    connection.youtube_channel_handle = account.get("handle") or snippet.get("customUrl") or connection.youtube_channel_handle
    connection.youtube_custom_url = account.get("custom_url") or snippet.get("customUrl") or connection.youtube_custom_url
    connection.subscribers_count = int(account.get("subscriber_count") or statistics.get("subscriberCount") or 0)
    connection.video_count = int(account.get("video_count") or statistics.get("videoCount") or 0)
    connection.view_count = int(account.get("view_count") or statistics.get("viewCount") or 0)
    connection.last_synced_at = timezone.now()
    connection.raw_profile_data = channel if isinstance(channel, dict) else {}
    connection.raw_channel_statistics = statistics
    connection.save(
        update_fields=[
            "youtube_channel_title",
            "youtube_channel_handle",
            "youtube_custom_url",
            "subscribers_count",
            "video_count",
            "view_count",
            "last_synced_at",
            "raw_profile_data",
            "raw_channel_statistics",
        ]
    )

    return JsonResponse(
        {
            "success": True,
            "connected": True,
            "youtube_channel_title": connection.youtube_channel_title,
            "youtube_channel_handle": connection.youtube_channel_handle,
            "subscribers_count": connection.subscribers_count,
            "video_count": connection.video_count,
            "view_count": connection.view_count,
            "failed_requests": snapshot_payload.get("failed_requests", []),
            "last_synced_at": connection.last_synced_at.isoformat(),
        }
    )
