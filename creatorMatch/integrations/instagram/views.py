import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

from creatorMatch.integrations.instagram.services.instagram_api import InstagramAPIError
from creatorMatch.integrations.instagram.services.instagram_oauth import InstagramOAuthService
from creatorMatch.integrations.models import IntegrationProvider, SocialAccount
from creatorMatch.integrations.services.oauth_state import OAuthStateError, consume_state, create_state

logger = logging.getLogger(__name__)


@login_required
@require_GET
def start_oauth(request: HttpRequest):
    redirect_path = request.GET.get("next", "/creators/settings/")
    state = create_state(
        user=request.user,
        provider=IntegrationProvider.INSTAGRAM,
        redirect_path=redirect_path,
    )
    auth_url = InstagramOAuthService().build_authorize_url(state)
    return JsonResponse({"authorization_url": auth_url})


@login_required
@require_GET
def callback(request: HttpRequest):
    error = request.GET.get("error")
    if error:
        description = request.GET.get("error_description", "OAuth canceled by user")
        return redirect(f"/creators/settings/?integration=instagram&status=failed&reason={description}")

    state = request.GET.get("state")
    code = request.GET.get("code")
    if not state or not code:
        return HttpResponseBadRequest("Missing state or authorization code")

    try:
        oauth_state = consume_state(
            user=request.user,
            provider=IntegrationProvider.INSTAGRAM,
            state=state,
        )
    except OAuthStateError:
        return redirect("/creators/settings/?integration=instagram&status=failed&reason=invalid_state")

    try:
        account = InstagramOAuthService().complete_oauth(user=request.user, code=code)
    except InstagramAPIError as exc:
        logger.exception("Instagram OAuth callback failed for user=%s", request.user.id)
        return redirect(f"{oauth_state.redirect_path}?integration=instagram&status=failed&reason={exc}")

    return redirect(
        f"{oauth_state.redirect_path}?integration=instagram&status=connected&username={account.username}"
    )


@login_required
@require_GET
def connection_status(request: HttpRequest):
    account = SocialAccount.objects.filter(
        user=request.user,
        provider=IntegrationProvider.INSTAGRAM,
    ).first()
    if not account:
        return JsonResponse(
            {
                "provider": "instagram",
                "connected": False,
                "status": "not_connected",
            }
        )

    expires_at = None
    token = getattr(account, "token", None)
    if token and token.expires_at:
        expires_at = token.expires_at.isoformat()

    return JsonResponse(
        {
            "provider": "instagram",
            "connected": account.connection_status == "connected",
            "status": account.connection_status,
            "username": account.username,
            "display_name": account.display_name,
            "account_id": account.external_account_id,
            "profile_url": account.profile_url,
            "last_synced_at": account.last_synced_at.isoformat() if account.last_synced_at else None,
            "last_sync_status": account.last_sync_status,
            "last_error": account.last_error,
            "token_expires_at": expires_at,
        }
    )


@login_required
@require_POST
def disconnect(request: HttpRequest):
    account = SocialAccount.objects.filter(
        user=request.user,
        provider=IntegrationProvider.INSTAGRAM,
    ).first()
    if not account:
        return JsonResponse({"detail": "Instagram account is not connected."}, status=404)

    InstagramOAuthService().disconnect(account=account)
    return JsonResponse({"detail": "Instagram account disconnected."})
