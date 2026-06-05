"""Gmail OAuth connection helpers for creator users.

Token protection note: this module centralizes token encode/decode so storage can
be upgraded to envelope encryption later. For this phase we use Django signing,
which provides tamper protection and keeps tokens out of logs/templates/JSON, but
it is not a substitute for database encryption if an attacker can read the DB and
Django SECRET_KEY.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone

from creators.models import GmailOAuthCredential

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GMAIL_PROFILE_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
SESSION_STATE_KEY = "gmail_oauth_state"
REQUEST_TIMEOUT_SECONDS = 15
TOKEN_EXPIRY_SKEW = timedelta(minutes=5)
TOKEN_SIGNING_SALT = "creators.gmail_oauth.token.v1"

# Least-privilege Gmail scopes for the upcoming outreach agent foundation:
# - gmail.readonly: search/read creator email threads for context.
# - gmail.compose: create/update drafts and send messages created as drafts.
# We intentionally do not request the broad mail.google.com scope in Phase 1.
GMAIL_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]


class GmailOAuthError(Exception):
    """Base class for user-safe Gmail OAuth failures."""

    default_message = "Unable to complete Gmail connection. Please try again."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message)
        self.user_message = message or self.default_message


class GmailOAuthConfigurationError(GmailOAuthError):
    default_message = "Gmail connection is not configured yet. Please contact support."


class GmailOAuthStateError(GmailOAuthError):
    default_message = "Gmail connection could not be verified. Please start again."


def _required_google_settings() -> tuple[str, str, str]:
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    client_secret = getattr(settings, "GOOGLE_CLIENT_SECRET", "")
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URI", "")
    if not client_id or not client_secret or not redirect_uri:
        raise GmailOAuthConfigurationError()
    return client_id, client_secret, redirect_uri


def encode_token(raw_token: str | None) -> str:
    if not raw_token:
        return ""
    return signing.dumps(raw_token, salt=TOKEN_SIGNING_SALT)


def decode_token(stored_token: str | None) -> str:
    if not stored_token:
        return ""
    try:
        return signing.loads(stored_token, salt=TOKEN_SIGNING_SALT)
    except signing.BadSignature as exc:
        raise GmailOAuthError("Stored Gmail credential could not be read. Please reconnect Gmail.") from exc


def _safe_error(message: str, detail: Any | None = None) -> str:
    if not detail:
        return message
    return f"{message} ({str(detail)[:200]})"


def _credential_for_update(user) -> GmailOAuthCredential:
    credential, _ = GmailOAuthCredential.objects.get_or_create(user=user)
    return credential


def build_gmail_authorization_url(request) -> str:
    client_id, _, redirect_uri = _required_google_settings()
    state = secrets.token_urlsafe(32)
    request.session[SESSION_STATE_KEY] = state
    request.session.modified = True

    has_refresh_token = False
    credential = getattr(request.user, "gmail_oauth_credential", None)
    if credential and credential.refresh_token:
        has_refresh_token = True

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_OAUTH_SCOPES),
        "state": state,
        "access_type": "offline",
        "include_granted_scopes": "true",
    }
    if not has_refresh_token:
        params["prompt"] = "consent"
    return f"{AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_gmail_callback(request) -> GmailOAuthCredential:
    _validate_callback_state(request)
    google_error = request.GET.get("error")
    if google_error:
        raise GmailOAuthError("Google did not authorize Gmail access. Please try again if you want to connect Gmail.")

    code = request.GET.get("code")
    if not code:
        raise GmailOAuthError("Google did not return an authorization code. Please start again.")

    token_payload = _exchange_code_for_tokens(code)
    refresh_token = token_payload.get("refresh_token") or ""
    access_token = token_payload.get("access_token") or ""
    if not access_token:
        raise GmailOAuthError("Google did not return an access token. Please start again.")

    existing = getattr(request.user, "gmail_oauth_credential", None)
    if not refresh_token and not (existing and existing.refresh_token):
        raise GmailOAuthError("Google did not return a refresh token. Please reconnect Gmail and approve offline access.")

    gmail_email = _fetch_gmail_email(access_token)
    credential = _credential_for_update(request.user)
    credential.access_token = encode_token(access_token)
    if refresh_token:
        credential.refresh_token = encode_token(refresh_token)
    credential.token_uri = token_payload.get("token_uri") or TOKEN_ENDPOINT
    credential.scopes = _normalise_scopes(token_payload.get("scope"))
    credential.expires_at = _expires_at(token_payload)
    credential.gmail_email = gmail_email or credential.gmail_email or ""
    credential.status = GmailOAuthCredential.STATUS_CONNECTED
    credential.last_error = "" if gmail_email else "Connected, but Gmail profile email could not be confirmed."
    credential.revoked_at = None
    credential.save()
    return credential


def _validate_callback_state(request) -> None:
    expected_state = request.session.pop(SESSION_STATE_KEY, "")
    request.session.modified = True
    received_state = request.GET.get("state", "")
    if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
        raise GmailOAuthStateError()


def _exchange_code_for_tokens(code: str) -> dict[str, Any]:
    client_id, client_secret, redirect_uri = _required_google_settings()
    try:
        response = requests.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GmailOAuthError("Google token exchange failed. Please try again.") from exc
    if response.status_code >= 400:
        raise GmailOAuthError("Google token exchange failed. Please try again.")
    try:
        return response.json()
    except ValueError as exc:
        raise GmailOAuthError("Google token exchange returned an invalid response. Please try again.") from exc


def _fetch_gmail_email(access_token: str) -> str:
    try:
        response = requests.get(
            GMAIL_PROFILE_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return ""
    if response.status_code >= 400:
        return ""
    try:
        payload = response.json()
    except ValueError:
        return ""
    return (payload.get("emailAddress") or "").strip()


def _normalise_scopes(scope_value: Any) -> list[str]:
    if isinstance(scope_value, str):
        scopes = [scope.strip() for scope in scope_value.split() if scope.strip()]
        return scopes or list(GMAIL_OAUTH_SCOPES)
    if isinstance(scope_value, list):
        return [str(scope).strip() for scope in scope_value if str(scope).strip()]
    return list(GMAIL_OAUTH_SCOPES)


def _expires_at(token_payload: dict[str, Any]) -> datetime | None:
    try:
        expires_in = int(token_payload.get("expires_in", 0))
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in <= 0:
        return None
    return timezone.now() + timedelta(seconds=expires_in)


def get_gmail_connection_status(user) -> dict[str, Any]:
    credential = getattr(user, "gmail_oauth_credential", None)
    if not credential:
        return {
            "connected": False,
            "status": GmailOAuthCredential.STATUS_DISCONNECTED,
            "gmail_email": "",
            "expires_at": None,
        }

    status = _computed_status(credential)
    expires_at = credential.expires_at.isoformat() if credential.expires_at else None
    return {
        "connected": status in {
            GmailOAuthCredential.STATUS_CONNECTED,
            GmailOAuthCredential.STATUS_EXPIRED_REFRESHABLE,
        },
        "status": status,
        "gmail_email": credential.gmail_email if credential.gmail_email else "",
        "expires_at": expires_at,
    }


def _computed_status(credential: GmailOAuthCredential) -> str:
    if credential.status in {
        GmailOAuthCredential.STATUS_DISCONNECTED,
        GmailOAuthCredential.STATUS_REVOKED,
        GmailOAuthCredential.STATUS_NEEDS_REAUTH,
        GmailOAuthCredential.STATUS_ERROR,
    }:
        return credential.status
    if credential.expires_at and credential.expires_at <= timezone.now():
        if credential.refresh_token:
            return GmailOAuthCredential.STATUS_EXPIRED_REFRESHABLE
        return GmailOAuthCredential.STATUS_NEEDS_REAUTH
    if credential.access_token:
        return GmailOAuthCredential.STATUS_CONNECTED
    return GmailOAuthCredential.STATUS_DISCONNECTED


def get_valid_gmail_access_token(user) -> str:
    credential = getattr(user, "gmail_oauth_credential", None)
    if not credential:
        raise GmailOAuthError("Gmail is not connected yet.")
    credential = refresh_gmail_token_if_needed(credential)
    if credential.status != GmailOAuthCredential.STATUS_CONNECTED:
        raise GmailOAuthError("Gmail needs to be reconnected before it can be used.")
    return decode_token(credential.access_token)


def refresh_gmail_token_if_needed(credential: GmailOAuthCredential) -> GmailOAuthCredential:
    if credential.status in {
        GmailOAuthCredential.STATUS_DISCONNECTED,
        GmailOAuthCredential.STATUS_REVOKED,
        GmailOAuthCredential.STATUS_NEEDS_REAUTH,
    }:
        return credential
    if credential.expires_at and credential.expires_at > timezone.now() + TOKEN_EXPIRY_SKEW:
        if credential.status != GmailOAuthCredential.STATUS_CONNECTED:
            credential.status = GmailOAuthCredential.STATUS_CONNECTED
            credential.save(update_fields=["status", "updated_at"])
        return credential
    if not credential.refresh_token:
        credential.status = GmailOAuthCredential.STATUS_NEEDS_REAUTH
        credential.last_error = "Refresh token is missing."
        credential.save(update_fields=["status", "last_error", "updated_at"])
        return credential

    client_id, client_secret, _ = _required_google_settings()
    refresh_token = decode_token(credential.refresh_token)
    try:
        response = requests.post(
            credential.token_uri or TOKEN_ENDPOINT,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        credential.status = GmailOAuthCredential.STATUS_ERROR
        credential.last_error = _safe_error("Google token refresh failed.", exc)
        credential.save(update_fields=["status", "last_error", "updated_at"])
        raise GmailOAuthError("Gmail token refresh failed. Please try again.") from exc

    if response.status_code >= 400:
        credential.status = GmailOAuthCredential.STATUS_NEEDS_REAUTH
        credential.last_error = "Google rejected the stored Gmail refresh token."
        credential.save(update_fields=["status", "last_error", "updated_at"])
        raise GmailOAuthError("Gmail needs to be reconnected.")

    try:
        payload = response.json()
    except ValueError as exc:
        credential.status = GmailOAuthCredential.STATUS_ERROR
        credential.last_error = "Google token refresh returned an invalid response."
        credential.save(update_fields=["status", "last_error", "updated_at"])
        raise GmailOAuthError("Gmail token refresh returned an invalid response.") from exc

    access_token = payload.get("access_token") or ""
    if not access_token:
        credential.status = GmailOAuthCredential.STATUS_ERROR
        credential.last_error = "Google token refresh did not return an access token."
        credential.save(update_fields=["status", "last_error", "updated_at"])
        raise GmailOAuthError("Gmail token refresh failed. Please reconnect Gmail.")

    credential.access_token = encode_token(access_token)
    credential.expires_at = _expires_at(payload)
    if payload.get("scope"):
        credential.scopes = _normalise_scopes(payload.get("scope"))
    credential.status = GmailOAuthCredential.STATUS_CONNECTED
    credential.last_error = ""
    credential.save(
        update_fields=["access_token", "expires_at", "scopes", "status", "last_error", "updated_at"]
    )
    return credential


def revoke_gmail_connection(user) -> None:
    credential = getattr(user, "gmail_oauth_credential", None)
    if not credential:
        return
    token = ""
    try:
        token = decode_token(credential.refresh_token) or decode_token(credential.access_token)
    except GmailOAuthError:
        token = ""
    if token:
        try:
            requests.post(
                REVOKE_ENDPOINT,
                params={"token": token},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            # Local disconnect still succeeds; the creator should not be trapped
            # because Google's revoke endpoint is temporarily unavailable.
            pass
    clear_gmail_connection(user, status=GmailOAuthCredential.STATUS_REVOKED)


def clear_gmail_connection(user, status: str = GmailOAuthCredential.STATUS_DISCONNECTED) -> None:
    credential = getattr(user, "gmail_oauth_credential", None)
    if not credential:
        return
    credential.access_token = ""
    credential.refresh_token = ""
    credential.gmail_email = ""
    credential.scopes = []
    credential.expires_at = None
    credential.status = status
    credential.last_error = ""
    credential.revoked_at = timezone.now() if status == GmailOAuthCredential.STATUS_REVOKED else None
    credential.save()
