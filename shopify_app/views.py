"""Views for interacting with Shopify."""

import hashlib
import hmac
import secrets
import uuid
from typing import Dict
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from merchants.models import MerchantMeta

from .discounts import select_discount_percentage
from .models import Shop
from .shopify_client import ShopifyClient


# Session key that stores the randomly generated OAuth state token.
STATE_SESSION_KEY = "shopify_oauth_state"


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_discount(request, merchant_uuid):
    """Create a discount code based on lottery probabilities."""
    try:
        meta = MerchantMeta.objects.get(uuid=merchant_uuid)
    except MerchantMeta.DoesNotExist:
        return Response({"error": "Invalid merchant"}, status=status.HTTP_400_BAD_REQUEST)

    if not meta.shopify_access_token or not meta.shopify_store_domain:
        return Response(
            {"error": "Missing Shopify credentials"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    percentage = select_discount_percentage()
    if percentage is None:
        return Response({"discount": None, "message": "No discount awarded"})

    client = ShopifyClient(meta.shopify_access_token, meta.shopify_store_domain)

    coupon_code = f"BADGER-{uuid.uuid4().hex[:8].upper()}"
    now = timezone.now()
    price_rule_payload = {
        "price_rule": {
            "title": coupon_code,
            "target_type": "line_item",
            "target_selection": "all",
            "allocation_method": "across",
            "value_type": "percentage",
            "value": f"-{float(percentage):.1f}",
            "customer_selection": "all",
            "starts_at": now.isoformat(),
            "ends_at": (now + timezone.timedelta(days=1)).isoformat(),
            "usage_limit": 1,
        }
    }

    response = client.post("/admin/api/2024-07/price_rules.json", json=price_rule_payload)
    price_rule_id = response.json()["price_rule"]["id"]

    discount_payload = {"discount_code": {"code": coupon_code}}
    client.post(
        f"/admin/api/2024-07/price_rules/{price_rule_id}/discount_codes.json",
        json=discount_payload,
    )

    return Response({"coupon_code": coupon_code, "discount": percentage})


@require_GET
def oauth_authorize(request: HttpRequest):
    """Begin the classic Shopify OAuth flow."""

    # Shopify requires the merchant's shop domain for OAuth. Reject the request
    # immediately when the `shop` query parameter is missing.
    shop = (request.GET.get("shop") or "").strip()
    if not shop:
        return HttpResponseBadRequest("Missing 'shop' parameter.")

    # Normalise user-supplied shop domain values by removing protocol prefixes
    # and trailing slashes. Shopify expects `example.myshopify.com` only.
    if shop.startswith("https://") or shop.startswith("http://"):
        shop = shop.split("://", 1)[1]
    shop = shop.strip("/")

    # Both the public API key and secret must exist before we can start OAuth.
    if not settings.SHOPIFY_API_KEY or not settings.SHOPIFY_API_SECRET:
        return JsonResponse(
            {"error": "Shopify API credentials are not configured."},
            status=500,
        )

    # Generate a cryptographically secure random string to defend against CSRF
    # attacks and store it in the session for later validation.
    state = _generate_state_token()
    request.session[STATE_SESSION_KEY] = state

    # Build the callback URL that Shopify will redirect the merchant back to.
    callback_url = request.build_absolute_uri(reverse("shopify_oauth_callback"))
    if callback_url.startswith("http://"):
        # Shopify requires an HTTPS redirect URI. When developing locally with
        # tunnelling tools we commonly receive an HTTP request, so upgrade it.
        callback_url = "https://" + callback_url.split("://", 1)[1]

    # Construct the Shopify authorization URL with the requested scopes.
    params = {
        "client_id": settings.SHOPIFY_API_KEY,
        "scope": "read_products,write_orders",
        "redirect_uri": callback_url,
        "state": state,
    }
    authorization_url = f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"

    # Redirect the merchant to Shopify where they can approve the installation.
    return redirect(authorization_url)


@require_GET
def oauth_callback(request: HttpRequest):
    """Handle Shopify's OAuth callback and persist the merchant token."""

    # Shopify sends all OAuth results as query parameters in the callback URL.
    # The `shop` and `code` parameters are mandatory for token exchange.
    shop = (request.GET.get("shop") or "").strip()
    code = (request.GET.get("code") or "").strip()
    if not shop or not code:
        return HttpResponseBadRequest("Missing required OAuth parameters.")

    # Ensure the request genuinely originated from Shopify by validating the
    # HMAC signature using the shared client secret.
    if not _validate_shopify_hmac(request.GET):
        return HttpResponseBadRequest("Invalid Shopify HMAC signature.")

    # Compare the `state` parameter against the value stored in the session to
    # confirm that the callback matches the session that initiated OAuth.
    expected_state = request.session.pop(STATE_SESSION_KEY, None)
    received_state = request.GET.get("state")
    if not expected_state or expected_state != received_state:
        return HttpResponseBadRequest("OAuth state mismatch.")

    # Exchange the short-lived authorization code for a permanent access token.
    try:
        access_token = _exchange_code_for_token(shop, code)
    except requests.RequestException as exc:
        return JsonResponse(
            {
                "error": "Failed to exchange access token with Shopify.",
                "details": str(exc),
            },
            status=500,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    # Persist the access token so that future API calls can be made on behalf of
    # the merchant. If the shop already exists we overwrite the stored token.
    Shop.objects.update_or_create(
        shop_domain=shop,
        defaults={"access_token": access_token},
    )

    # Provide a simple confirmation payload to the merchant (or installer) so
    # they know the OAuth process completed successfully.
    return JsonResponse({"status": "ok", "shop": shop})


def _generate_state_token() -> str:
    """Return a cryptographically secure random string for OAuth state."""

    return secrets.token_urlsafe(32)


def _validate_shopify_hmac(params: Dict[str, str]) -> bool:
    """Validate the request signature provided by Shopify."""

    provided_hmac = params.get("hmac")
    if not provided_hmac or not settings.SHOPIFY_API_SECRET:
        return False

    # Shopify requires that parameters are sorted lexicographically, joined as
    # `key=value` pairs and concatenated with `&` before signing the message.
    message_parts = []
    for key in sorted(k for k in params.keys() if k != "hmac"):
        values = params.getlist(key) if hasattr(params, "getlist") else [params[key]]
        message_parts.append(f"{key}={','.join(values)}")
    message = "&".join(message_parts)

    digest = hmac.new(
        settings.SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(digest, provided_hmac)


def _exchange_code_for_token(shop: str, code: str) -> str:
    """Exchange the OAuth authorization code for an access token."""

    if not settings.SHOPIFY_API_KEY or not settings.SHOPIFY_API_SECRET:
        raise ValueError("Shopify API credentials are not configured.")

    url = f"https://{shop}/admin/oauth/access_token"
    payload = {
        "client_id": settings.SHOPIFY_API_KEY,
        "client_secret": settings.SHOPIFY_API_SECRET,
        "code": code,
    }

    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    data = response.json()

    access_token = data.get("access_token")
    if not access_token:
        raise ValueError("Shopify response did not include an access token.")

    return access_token
