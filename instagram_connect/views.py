import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from creators.services.social_dashboard import InstagramAnalyticsService

from .models import InstagramConnection
from .services import (
    MetaAPIError,
    build_oauth_url,
    exchange_code_for_access_token,
    exchange_for_long_lived_access_token,
    generate_oauth_state,
    get_facebook_user,
    get_instagram_user,
    get_page_instagram_business_account,
    get_user_pages,
    resolve_meta_oauth_scopes,
    should_refresh_token,
    token_expiry_from_response,
)

logger = logging.getLogger(__name__)

OAUTH_PERMISSION_REASONS = {
    "public_profile": "identify the logged-in Facebook user",
    "pages_show_list": "list Facebook Pages the user can manage",
    "pages_read_engagement": "read Page-level engagement data and metadata",
    "instagram_basic": "read Instagram Business/Creator account profile fields",
    "instagram_manage_insights": "read Instagram insights and analytics metrics",
}


def _ensure_fresh_access_token(
    connection: InstagramConnection, *, force_refresh: bool = False
) -> None:
    if not force_refresh and not should_refresh_token(connection.token_expires_at):
        return

    refreshed_token_data = exchange_for_long_lived_access_token(connection.access_token)
    refreshed_access_token = refreshed_token_data.get("access_token")
    if not refreshed_access_token:
        raise MetaAPIError("Meta did not return a refreshed access token.")

    connection.access_token = refreshed_access_token
    connection.user_access_token = refreshed_access_token
    connection.token_expires_at = token_expiry_from_response(refreshed_token_data)
    connection.save(update_fields=["access_token", "user_access_token", "token_expires_at"])


def _is_token_invalid_error(error: MetaAPIError) -> bool:
    message = str(error).lower()
    return "invalid oauth" in message or "session has expired" in message or "expired" in message


def _normalise_sync_error_message(error: MetaAPIError) -> str:
    if _is_token_invalid_error(error):
        return "Meta access token has expired or been revoked. Please reconnect Instagram."
    return str(error).strip() or "Unable to sync Instagram metrics right now. Please try again."


def _select_page_with_instagram_account(access_token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    pages = get_user_pages(access_token)
    if not pages:
        raise MetaAPIError(
            "No Facebook Pages were found. Connect a Page and grant pages_show_list/pages_read_engagement permissions."
        )

    for page in pages:
        page_id = str(page.get("id") or "").strip()
        page_token = page.get("access_token") or access_token
        if not page_id:
            continue
        ig_account = get_page_instagram_business_account(page_id, page_token)
        if ig_account:
            return page, ig_account

    raise MetaAPIError(
        "No Instagram Business/Creator account linked to any authorized Facebook Page."
    )


@login_required
@require_GET
def connect_instagram(request):
    action = "reconnect_instagram" if hasattr(request.user, "instagram_connection") else "connect_instagram"
    state = generate_oauth_state()
    request.session["meta_oauth_state"] = state
    oauth_url = build_oauth_url(state)
    requested_permissions = [scope.strip() for scope in resolve_meta_oauth_scopes().split(",") if scope.strip()]
    permission_reasons = {
        permission: OAUTH_PERMISSION_REASONS.get(
            permission,
            "required by current Meta app flow configuration",
        )
        for permission in requested_permissions
    }
    print(
        "[instagram_oauth] connect route selected",
        {
            "action": action,
            "user_id": request.user.id,
            "requested_permissions": requested_permissions,
            "permission_reasons": permission_reasons,
            "redirect_url": oauth_url,
            "state": state,
        },
        flush=True,
    )
    return redirect(oauth_url)


@login_required
@require_GET
def instagram_callback(request):
    settings_url = reverse("creator_settings")
    print(
        "[instagram_oauth] callback received",
        {
            "user_id": request.user.id,
            "query_params": dict(request.GET),
        },
        flush=True,
    )

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
            raise MetaAPIError("Meta did not return an access token.")

        long_lived_token_data = exchange_for_long_lived_access_token(access_token)
        long_lived_access_token = long_lived_token_data.get("access_token")
        if long_lived_access_token:
            token_data = long_lived_token_data
            access_token = long_lived_access_token

        fb_user = get_facebook_user(access_token)
        page, ig_account = _select_page_with_instagram_account(access_token)

        page_id = str(page.get("id") or "")
        page_name = page.get("name") or ""
        page_access_token = page.get("access_token") or access_token
        ig_user_id = str(ig_account.get("id") or "")
        if not ig_user_id:
            raise MetaAPIError("Linked Instagram account is missing an id.")

        ig_profile = get_instagram_user(ig_user_id, access_token)

        now = timezone.now()
        connection, created = InstagramConnection.objects.update_or_create(
            user=request.user,
            defaults={
                "facebook_user_id": str(fb_user.get("id") or ""),
                "page_id": page_id,
                "page_name": page_name,
                "instagram_user_id": ig_user_id,
                "instagram_username": ig_profile.get("username") or ig_account.get("username") or "",
                "followers_count": int(ig_profile.get("followers_count") or ig_account.get("followers_count") or 0),
                "media_count": int(ig_profile.get("media_count") or ig_account.get("media_count") or 0),
                "access_token": access_token,
                "user_access_token": access_token,
                "page_access_token": page_access_token,
                "token_expires_at": token_expiry_from_response(token_data),
                "last_synced_at": now,
            },
        )

        if created:
            connection.connected_at = now
            connection.save(update_fields=["connected_at"])

    except MetaAPIError as exc:
        logger.warning("Instagram Meta OAuth callback failed: %s", exc)
        return redirect(f"{settings_url}?instagram_oauth=error")
    finally:
        request.session.pop("meta_oauth_state", None)

    return redirect(f"{settings_url}?instagram_oauth=success")


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
        logger.warning("Unable to refresh Meta token for status endpoint: %s", exc)

    return JsonResponse(
        {
            "connected": True,
            "facebook_user_id": connection.facebook_user_id,
            "page_id": connection.page_id,
            "page_name": connection.page_name,
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
        ig_user = get_instagram_user(connection.instagram_user_id, connection.access_token)
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
    connection.save(update_fields=["instagram_username", "followers_count", "media_count", "last_synced_at"])

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
