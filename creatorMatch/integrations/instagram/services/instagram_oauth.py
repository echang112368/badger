from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from creatorMatch.integrations.instagram.services.instagram_api import InstagramGraphClient
from creatorMatch.integrations.models import (
    ConnectionStatus,
    IntegrationProvider,
    SocialAccount,
    SocialAccountToken,
)
from creatorMatch.integrations.services.security import encrypt_token


class InstagramOAuthService:
    provider = IntegrationProvider.INSTAGRAM

    def __init__(self):
        self.client = InstagramGraphClient()

    def build_authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": settings.INSTAGRAM_APP_ID,
                "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
                "scope": settings.INSTAGRAM_SCOPES,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{settings.INSTAGRAM_AUTH_BASE_URL.rstrip('/')}/oauth/authorize?{query}"

    @transaction.atomic
    def complete_oauth(self, *, user, code: str) -> SocialAccount:
        short_payload = self.client.exchange_code_for_short_token(code)

        access_token = short_payload.access_token
        token_type = short_payload.token_type
        expires_at = short_payload.expires_at
        token_metadata = {"token_variant": "short_lived"}

        if getattr(settings, "INSTAGRAM_USE_LONG_LIVED_TOKEN", True):
            long_payload = self.client.exchange_for_long_lived_token(short_payload.access_token)
            access_token = long_payload["access_token"]
            expires_in = long_payload.get("expires_in")
            expires_at = (
                timezone.now() + timedelta(seconds=expires_in)
                if expires_in
                else expires_at
            )
            token_type = long_payload.get("token_type", token_type)
            token_metadata = {"token_variant": "long_lived", "raw": long_payload}

        profile = self.client.get_profile(access_token)
        account, _ = SocialAccount.objects.update_or_create(
            user=user,
            provider=self.provider,
            defaults={
                "external_account_id": str(profile["id"]),
                "username": profile.get("username", ""),
                "display_name": profile.get("username", ""),
                "profile_url": f"https://instagram.com/{profile.get('username', '')}",
                "scopes": [scope.strip() for scope in settings.INSTAGRAM_SCOPES.split(",") if scope.strip()],
                "account_metadata": profile,
                "connection_status": ConnectionStatus.CONNECTED,
                "last_error": "",
                "disconnected_at": None,
            },
        )

        SocialAccountToken.objects.update_or_create(
            social_account=account,
            defaults={
                "access_token_encrypted": encrypt_token(access_token),
                "refresh_token_encrypted": "",
                "token_type": token_type,
                "expires_at": expires_at,
                "token_metadata": token_metadata,
                "invalidated_at": None,
            },
        )
        return account

    @transaction.atomic
    def disconnect(self, *, account: SocialAccount) -> None:
        account.connection_status = ConnectionStatus.DISCONNECTED
        account.disconnected_at = timezone.now()
        account.last_error = ""
        account.save(update_fields=["connection_status", "disconnected_at", "last_error", "updated_at"])
        token = getattr(account, "token", None)
        if token:
            token.invalidated_at = timezone.now()
            token.access_token_encrypted = ""
            token.refresh_token_encrypted = ""
            token.save(update_fields=["invalidated_at", "access_token_encrypted", "refresh_token_encrypted", "updated_at"])
