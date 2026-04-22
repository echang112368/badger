from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.shortcuts import redirect

from .models import InstagramConnection
from creators.services.social_dashboard import InstagramAnalyticsService
from .services import (
    MetaAPIError,
    build_oauth_url,
    exchange_code_for_access_token,
    generate_oauth_state,
    get_instagram_user,
    token_expiry_from_response,
)


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
    """Complete OAuth callback, persist the connected Instagram account, and return JSON."""

    if request.GET.get("error"):
        return JsonResponse(
            {
                "success": False,
                "error": "oauth_denied",
                "message": request.GET.get(
                    "error_description",
                    "Authorization was denied by Meta.",
                ),
            },
            status=400,
        )

    code = request.GET.get("code")
    if not code:
        return JsonResponse(
            {
                "success": False,
                "error": "missing_code",
                "message": "Missing authorization code from Meta callback.",
            },
            status=400,
        )

    expected_state = request.session.get("meta_oauth_state")
    received_state = request.GET.get("state")
    if not expected_state or expected_state != received_state:
        return JsonResponse(
            {
                "success": False,
                "error": "invalid_state",
                "message": "OAuth state validation failed.",
            },
            status=400,
        )

    try:
        token_data = exchange_code_for_access_token(code)
        access_token = token_data.get("access_token")
        if not access_token:
            return JsonResponse(
                {
                    "success": False,
                    "error": "missing_access_token",
                    "message": "Meta did not return an access token.",
                },
                status=400,
            )

        ig_user = get_instagram_user(access_token)
        ig_user_id = ig_user.get("user_id") or ig_user.get("id")
        if not ig_user_id:
            return JsonResponse(
                {
                    "success": False,
                    "error": "missing_instagram_id",
                    "message": "Instagram user ID was missing from Meta response.",
                },
                status=400,
            )

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

    except MetaAPIError as exc:
        return JsonResponse(
            {
                "success": False,
                "error": "meta_api_error",
                "message": str(exc),
            },
            status=400,
        )
    finally:
        request.session.pop("meta_oauth_state", None)

    return JsonResponse(
        {
            "success": True,
            "connected": True,
            "instagram_user_id": connection.instagram_user_id,
            "instagram_username": connection.instagram_username,
            "followers_count": connection.followers_count,
            "media_count": connection.media_count,
            "last_synced_at": connection.last_synced_at.isoformat()
            if connection.last_synced_at
            else None,
        }
    )


@login_required
@require_GET
def instagram_status(request):
    """Return the current Instagram connection status for the logged-in user."""

    try:
        connection = request.user.instagram_connection
    except InstagramConnection.DoesNotExist:
        return JsonResponse({"connected": False})

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
        ig_user = get_instagram_user(connection.access_token)
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
