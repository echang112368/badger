from typing import Any

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.shortcuts import redirect

from .models import InstagramConnection
from creators.services.social_dashboard import InstagramAnalyticsService
from .services import (
    MetaAPIError,
    build_oauth_url,
    exchange_code_for_access_token,
    exchange_for_long_lived_access_token,
    generate_oauth_state,
    get_instagram_user,
    refresh_long_lived_access_token,
    should_refresh_token,
    token_expiry_from_response,
)


def _ensure_fresh_access_token(connection: InstagramConnection) -> None:
    if not should_refresh_token(connection.token_expires_at):
        return

    refreshed_token_data = refresh_long_lived_access_token(connection.access_token)
    refreshed_access_token = refreshed_token_data.get("access_token")
    if not refreshed_access_token:
        raise MetaAPIError("Meta did not return a refreshed access token.")

    connection.access_token = refreshed_access_token
    connection.token_expires_at = token_expiry_from_response(refreshed_token_data)
    connection.save(update_fields=["access_token", "token_expires_at"])


def _is_token_invalid_error(error: MetaAPIError) -> bool:
    message = str(error).lower()
    return "session key invalid" in message or "invalid oauth access token" in message


def _get_instagram_user_with_latest_token(
    connection: InstagramConnection,
) -> dict[str, Any]:
    try:
        return get_instagram_user(connection.access_token)
    except MetaAPIError as exc:
        if not _is_token_invalid_error(exc):
            raise

    refreshed_token_data = refresh_long_lived_access_token(connection.access_token)
    refreshed_access_token = refreshed_token_data.get("access_token")
    if not refreshed_access_token:
        raise MetaAPIError(
            "Instagram access token is no longer valid. Please reconnect Instagram."
        )

    connection.access_token = refreshed_access_token
    connection.token_expires_at = token_expiry_from_response(refreshed_token_data)
    connection.save(update_fields=["access_token", "token_expires_at"])

    try:
        return get_instagram_user(connection.access_token)
    except MetaAPIError as exc:
        if _is_token_invalid_error(exc):
            raise MetaAPIError(
                "Instagram access token is no longer valid. Please reconnect Instagram."
            ) from exc
        raise


@login_required
@require_GET
def connect_instagram(request):
    """Start Meta OAuth for the logged-in user."""

    state = generate_oauth_state()
    request.session["meta_oauth_state"] = state
    oauth_url = build_oauth_url(state)
    print("META_REDIRECT_URI:", settings.META_REDIRECT_URI)
    print("OAuth URL:", oauth_url)
    return redirect(oauth_url)


@login_required
@require_GET
def instagram_callback(request):
    """Complete OAuth callback, persist account data, and send user back to Settings."""

    settings_url = reverse("creator_settings")

    if request.GET.get("error"):
        return redirect(f"{settings_url}?instagram_oauth=error")

    code = request.GET.get("code")
    if not code:
        return redirect(f"{settings_url}?instagram_oauth=error")

    expected_state = request.session.get("meta_oauth_state")
    received_state = request.GET.get("state")
    if not expected_state or expected_state != received_state:
        return redirect(f"{settings_url}?instagram_oauth=error")

    try:
        token_data = exchange_code_for_access_token(code)
        access_token = token_data.get("access_token")
        if not access_token:
            return redirect(f"{settings_url}?instagram_oauth=error")

        expires_in = token_data.get("expires_in")
        if isinstance(expires_in, int) and expires_in <= 2 * 60 * 60:
            long_lived_token_data = exchange_for_long_lived_access_token(access_token)
            long_lived_access_token = long_lived_token_data.get("access_token")
            if not long_lived_access_token:
                raise MetaAPIError("Meta did not return a long-lived access token.")
            token_data = long_lived_token_data
            access_token = long_lived_access_token

        ig_user = get_instagram_user(access_token)
        ig_user_id = ig_user.get("user_id") or ig_user.get("id")
        if not ig_user_id:
            return redirect(f"{settings_url}?instagram_oauth=error")

        now = timezone.now()

        connection, created = InstagramConnection.objects.update_or_create(
            user=request.user,
            defaults={
                "facebook_user_id": "",
                "page_id": "",
                "page_name": "",
                "instagram_user_id": str(ig_user.get("id") or ig_user_id),
                "instagram_username": ig_user.get("username") or "",
                "followers_count": int(ig_user.get("followers_count") or 0) if ig_user.get("followers_count") else 0,
                "media_count": int(ig_user.get("media_count") or 0) if ig_user.get("media_count") else 0,
                "access_token": access_token,
                "token_expires_at": token_expiry_from_response(token_data),
                "last_synced_at": now,
            },
        )

        if created:
            connection.connected_at = now
            connection.save(update_fields=["connected_at"])

    except MetaAPIError:
        return redirect(f"{settings_url}?instagram_oauth=error")
    finally:
        request.session.pop("meta_oauth_state", None)

    return redirect(f"{settings_url}?instagram_oauth=success")


@login_required
@require_GET
def instagram_status(request):
    """Return the current Instagram connection status for the logged-in user."""

    try:
        connection = request.user.instagram_connection
    except InstagramConnection.DoesNotExist:
        return JsonResponse({"connected": False})

    try:
        _ensure_fresh_access_token(connection)
    except MetaAPIError:
        # Preserve status visibility even if token refresh fails.
        pass

    return JsonResponse(
        {
            "connected": True,
            "instagram_username": connection.instagram_username,
            "instagram_user_id": connection.instagram_user_id,
            "followers_count": connection.followers_count,
            "media_count": connection.media_count,
            "last_synced_at": connection.last_synced_at.isoformat()
            if connection.last_synced_at
            else None,
            "connected_at": connection.connected_at.isoformat()
            if connection.connected_at
            else None,
            "token_expires_at": connection.token_expires_at.isoformat()
            if connection.token_expires_at
            else None,
        }
    )


@login_required
@require_GET
def instagram_sync(request):
    """Refresh and persist Instagram metrics for an existing connection."""

    try:
        connection = request.user.instagram_connection
    except InstagramConnection.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "not_connected",
                "message": "No Instagram connection found for this account.",
            },
            status=404,
        )

    try:
        _ensure_fresh_access_token(connection)
        ig_user = _get_instagram_user_with_latest_token(connection)
        snapshot_payload = InstagramAnalyticsService(request.user).fetch_and_cache(
            connection
        )
    except MetaAPIError as exc:
        return JsonResponse(
            {
                "success": False,
                "error": "meta_api_error",
                "message": str(exc),
            },
            status=400,
        )

    connection.instagram_username = ig_user.get("username") or connection.instagram_username
    connection.followers_count = int(
        snapshot_payload.get("account", {}).get("followers_count")
        or ig_user.get("followers_count")
        or 0
    )
    connection.media_count = int(
        snapshot_payload.get("account", {}).get("media_count")
        or ig_user.get("media_count")
        or 0
    )
    connection.last_synced_at = timezone.now()
    connection.save(
        update_fields=[
            "instagram_username",
            "followers_count",
            "media_count",
            "last_synced_at",
        ]
    )

    return JsonResponse(
        {
            "success": True,
            "connected": True,
            "instagram_username": connection.instagram_username,
            "followers_count": connection.followers_count,
            "media_count": connection.media_count,
            "last_synced_at": connection.last_synced_at.isoformat(),
        }
    )
