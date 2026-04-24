import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from creators.models import SocialAnalyticsSnapshot
from creators.services.social_dashboard import InstagramAnalyticsService

from .models import InstagramConnection
from .services import (
    MetaAPIError,
    build_oauth_url,
    exchange_code_for_access_token,
    exchange_for_long_lived_access_token,
    generate_oauth_state,
    get_instagram_user,
    refresh_long_lived_access_token,
    resolve_meta_oauth_scopes,
    should_refresh_token,
    token_expiry_from_response,
)

logger = logging.getLogger(__name__)

OAUTH_PERMISSION_REASONS = {
    "instagram_business_basic": "read Instagram Business/Creator account profile fields",
    "instagram_business_manage_insights": "read Instagram insights and analytics metrics",
    "instagram_business_manage_comments": "read and moderate comments for connected media",
    "instagram_business_content_publish": "required for media publishing permissions",
}


def _ensure_fresh_access_token(connection: InstagramConnection, *, force_refresh: bool = False) -> None:
    if not force_refresh and not should_refresh_token(connection.token_expires_at):
        return

    refreshed_token_data = refresh_long_lived_access_token(connection.instagram_access_token)
    refreshed_access_token = refreshed_token_data.get("access_token")
    if not refreshed_access_token:
        raise MetaAPIError("Instagram did not return a refreshed access token.")

    connection.instagram_access_token = refreshed_access_token
    connection.token_expires_at = token_expiry_from_response(refreshed_token_data)
    connection.save(update_fields=["instagram_access_token", "token_expires_at"])


def _normalise_sync_error_message(error: MetaAPIError) -> str:
    message = str(error).lower()
    if "invalid oauth" in message or "session has expired" in message or "expired" in message:
        return "Instagram access token has expired or been revoked. Please reconnect Instagram."
    return str(error).strip() or "Unable to sync Instagram metrics right now. Please try again."


@login_required
@require_GET
def connect_instagram(request):
    state = generate_oauth_state()
    request.session["instagram_oauth_state"] = state
    oauth_url = build_oauth_url(state)
    requested_permissions = [scope.strip() for scope in resolve_meta_oauth_scopes().split(",") if scope.strip()]
    logger.info(
        "[instagram_oauth] connect route selected",
        extra={
            "user_id": request.user.id,
            "requested_permissions": requested_permissions,
        },
    )
    return redirect(oauth_url)


@login_required
@require_GET
def instagram_callback(request):
    settings_url = reverse("creator_settings")

    if request.GET.get("error"):
        return redirect(f"{settings_url}?instagram_oauth=error")

    code = request.GET.get("code")
    if not code:
        return redirect(f"{settings_url}?instagram_oauth=error")

    expected_state = request.session.get("instagram_oauth_state")
    received_state = request.GET.get("state")
    if not expected_state or expected_state != received_state:
        return redirect(f"{settings_url}?instagram_oauth=error")

    try:
        token_data = exchange_code_for_access_token(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise MetaAPIError("Instagram did not return an access token.")

        long_lived_token_data = exchange_for_long_lived_access_token(access_token)
        long_lived_access_token = long_lived_token_data.get("access_token")
        if long_lived_access_token:
            token_data = long_lived_token_data
            access_token = long_lived_access_token

        ig_profile = get_instagram_user(access_token)
        ig_user_id = str(ig_profile.get("id") or "")
        if not ig_user_id:
            raise MetaAPIError("Instagram profile response is missing an id.")

        now = timezone.now()
        connection, created = InstagramConnection.objects.update_or_create(
            user=request.user,
            defaults={
                "platform": InstagramConnection.PLATFORM_INSTAGRAM,
                "instagram_user_id": ig_user_id,
                "instagram_username": ig_profile.get("username") or "",
                "followers_count": int(ig_profile.get("followers_count") or 0),
                "media_count": int(ig_profile.get("media_count") or 0),
                "instagram_access_token": access_token,
                "token_expires_at": token_expiry_from_response(token_data),
                "last_synced_at": now,
                "raw_profile_data": ig_profile,
            },
        )

        if created:
            connection.connected_at = now
            connection.save(update_fields=["connected_at"])

    except MetaAPIError as exc:
        logger.warning("Instagram OAuth callback failed: %s", exc)
        return redirect(f"{settings_url}?instagram_oauth=error")
    finally:
        request.session.pop("instagram_oauth_state", None)

    return redirect(f"{settings_url}?instagram_oauth=success")


@login_required
@require_POST
def instagram_disconnect(request):
    SocialAnalyticsSnapshot.objects.filter(
        user=request.user,
        platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
    ).delete()
    InstagramConnection.objects.filter(user=request.user).delete()
    request.session.pop("instagram_oauth_state", None)

    return redirect(f"{reverse('creator_settings')}?instagram_disconnect=success")


@login_required
@require_GET
def instagram_status(request):
    try:
        connection = request.user.instagram_connection
    except InstagramConnection.DoesNotExist:
        return JsonResponse({"connected": False})

    try:
        _ensure_fresh_access_token(connection)
    except MetaAPIError as exc:
        logger.warning("Unable to refresh Instagram token for status endpoint: %s", exc)

    return JsonResponse(
        {
            "connected": True,
            "instagram_username": connection.instagram_username,
            "instagram_user_id": connection.instagram_user_id,
            "followers_count": connection.followers_count,
            "media_count": connection.media_count,
            "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
            "connected_at": connection.connected_at.isoformat() if connection.connected_at else None,
            "token_expires_at": connection.token_expires_at.isoformat() if connection.token_expires_at else None,
        }
    )


@login_required
@require_GET
def instagram_sync(request):
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
        _ensure_fresh_access_token(connection, force_refresh=True)
        ig_user = get_instagram_user(connection.instagram_access_token, connection.instagram_user_id)
        snapshot_payload = InstagramAnalyticsService(request.user).fetch_and_cache(connection)
    except MetaAPIError as exc:
        return JsonResponse(
            {
                "success": False,
                "error": "meta_api_error",
                "message": _normalise_sync_error_message(exc),
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
    connection.raw_profile_data = ig_user if isinstance(ig_user, dict) else {}
    connection.save(
        update_fields=[
            "instagram_username",
            "followers_count",
            "media_count",
            "last_synced_at",
            "raw_profile_data",
        ]
    )

    return JsonResponse(
        {
            "success": True,
            "connected": True,
            "instagram_username": connection.instagram_username,
            "followers_count": connection.followers_count,
            "media_count": connection.media_count,
            "failed_requests": snapshot_payload.get("failed_requests", []),
            "last_synced_at": connection.last_synced_at.isoformat(),
        }
    )
