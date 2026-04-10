import secrets
from datetime import timedelta

from django.utils import timezone

from creatorMatch.integrations.models import OAuthState


class OAuthStateError(Exception):
    pass


def create_state(*, user, provider: str, redirect_path: str = "/creators/settings/") -> str:
    state = secrets.token_urlsafe(32)
    OAuthState.objects.create(
        user=user,
        provider=provider,
        state=state,
        redirect_path=redirect_path,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    return state


def consume_state(*, user, provider: str, state: str) -> OAuthState:
    oauth_state = OAuthState.objects.filter(
        user=user,
        provider=provider,
        state=state,
    ).first()
    if not oauth_state:
        raise OAuthStateError("Missing or invalid OAuth state")
    if not oauth_state.is_valid:
        raise OAuthStateError("OAuth state expired or already used")

    oauth_state.consumed_at = timezone.now()
    oauth_state.save(update_fields=["consumed_at"])
    return oauth_state
