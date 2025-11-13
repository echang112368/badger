"""Shopify OAuth helpers and state management."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
from typing import Mapping, MutableMapping, Optional
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.urls import reverse


STATE_SESSION_KEY = "shopify_oauth_state"
CALLBACK_SESSION_KEY = "shopify_oauth_redirect"


class ShopifyOAuthError(RuntimeError):
    """Raised when the Shopify OAuth handshake fails."""


@dataclass(frozen=True)
class AccessTokenResponse:
    """Normalised Shopify access token details."""

    access_token: str
    scope: str
    associated_user_scope: str
    raw: Mapping[str, object]


def normalise_shop_domain(domain: str) -> str:
    """Return a normalised Shopify shop domain."""

    if not domain:
        return ""

    value = domain.strip().lower()
    if value.startswith("https://") or value.startswith("http://"):
        value = value.split("://", 1)[1]
    value = value.strip("/")

    return value


def session_token_key(domain: str) -> str:
    """Return the session key used to cache temporary Shopify tokens."""

    return f"shopify_install_token:{normalise_shop_domain(domain)}"


def session_scope_key(domain: str) -> str:
    """Return the session key that stores authorised scopes for a store."""

    return f"shopify_install_scope:{normalise_shop_domain(domain)}"


def generate_state_token() -> str:
    """Return a cryptographically secure random string for OAuth state."""

    return secrets.token_urlsafe(32)


def validate_shopify_hmac(params: Mapping[str, str]) -> bool:
    """Validate the request signature provided by Shopify."""

    provided_hmac = params.get("hmac")
    secret = getattr(settings, "SHOPIFY_API_SECRET", "")
    if not provided_hmac or not secret:
        return False

    message_parts = []
    for key in sorted(k for k in params.keys() if k != "hmac"):
        value = params.get(key)
        if value is None:
            continue

        if hasattr(params, "getlist"):
            values = params.getlist(key)
        else:
            values = [value]

        message_parts.append(f"{key}={','.join(values)}")

    message = "&".join(message_parts)
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, provided_hmac)


def _build_redirect_uri(request) -> str:
    configured_redirect = getattr(settings, "SHOPIFY_REDIRECT_URI", "") or ""
    if configured_redirect:
        return configured_redirect

    callback = request.build_absolute_uri(reverse("shopify_oauth_callback"))
    if callback.startswith("http://"):
        callback = "https://" + callback.split("://", 1)[1]

    return callback


def _resolve_scopes() -> str:
    scopes = getattr(settings, "SHOPIFY_SCOPES", "")
    if isinstance(scopes, (list, tuple, set)):
        value = ",".join(str(scope) for scope in scopes if scope)
    else:
        value = str(scopes)

    return value.strip() or "read_products,write_discounts"


def build_authorization_url(shop: str, state: str, redirect_uri: str) -> str:
    params = {
        "client_id": settings.SHOPIFY_API_KEY,
        "scope": _resolve_scopes(),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"


def exchange_code_for_token(
    shop: str, code: str, *, redirect_uri: Optional[str] = None
) -> AccessTokenResponse:
    """Exchange the OAuth authorization code for an access token."""

    if not settings.SHOPIFY_API_KEY or not settings.SHOPIFY_API_SECRET:
        raise ShopifyOAuthError("Shopify API credentials are not configured.")

    payload: MutableMapping[str, object] = {
        "client_id": settings.SHOPIFY_API_KEY,
        "client_secret": settings.SHOPIFY_API_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }

    if redirect_uri:
        payload["redirect_uri"] = redirect_uri

    try:
        response = requests.post(
            f"https://{shop}/admin/oauth/access_token",
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:  # pragma: no cover - network failures
        raise ShopifyOAuthError("Failed to exchange access token with Shopify.") from exc

    data = response.json()
    token = data.get("access_token")
    if not token:
        raise ShopifyOAuthError("Shopify response did not include an access token.")

    scope = str(data.get("scope", "") or "")
    associated_scope = str(data.get("associated_user_scope", "") or "")

    return AccessTokenResponse(
        access_token=str(token),
        scope=scope,
        associated_user_scope=associated_scope,
        raw=data,
    )


class ShopifyOAuthService:
    """Encapsulates Shopify OAuth state transitions."""

    def __init__(self, request):
        self.request = request

    def begin_installation(self, shop: str) -> str:
        normalised = normalise_shop_domain(shop)
        if not normalised:
            raise ShopifyOAuthError("Missing 'shop' parameter.")

        state = generate_state_token()
        self.request.session[STATE_SESSION_KEY] = state

        redirect_uri = _build_redirect_uri(self.request)
        self.request.session[CALLBACK_SESSION_KEY] = redirect_uri

        if not settings.SHOPIFY_API_KEY:
            raise ShopifyOAuthError("Shopify API credentials are not configured.")

        return build_authorization_url(normalised, state, redirect_uri)

    def complete_installation(self, params: Mapping[str, str]) -> AccessTokenResponse:
        if not validate_shopify_hmac(params):
            raise ShopifyOAuthError("Invalid Shopify HMAC signature.")

        expected_state = self.request.session.pop(STATE_SESSION_KEY, "")
        received_state = params.get("state", "")
        if not expected_state or expected_state != received_state:
            raise ShopifyOAuthError("OAuth state mismatch.")

        shop = normalise_shop_domain(params.get("shop", ""))
        if not shop:
            raise ShopifyOAuthError("Missing 'shop' parameter.")

        code = (params.get("code") or "").strip()
        if not code:
            raise ShopifyOAuthError("Missing authorization code.")

        redirect_uri = self.request.session.pop(CALLBACK_SESSION_KEY, None)
        if not redirect_uri:
            redirect_uri = _build_redirect_uri(self.request)

        token_response = exchange_code_for_token(shop, code, redirect_uri=redirect_uri)
        self.request.session[session_token_key(shop)] = token_response.access_token
        self.request.session[session_scope_key(shop)] = token_response.scope

        return token_response


__all__ = [
    "AccessTokenResponse",
    "CALLBACK_SESSION_KEY",
    "STATE_SESSION_KEY",
    "ShopifyOAuthError",
    "ShopifyOAuthService",
    "build_authorization_url",
    "exchange_code_for_token",
    "generate_state_token",
    "normalise_shop_domain",
    "session_scope_key",
    "session_token_key",
    "validate_shopify_hmac",
]
