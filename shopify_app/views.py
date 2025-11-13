"""Views for interacting with Shopify."""

import hashlib
import hmac
import secrets
import uuid
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from merchants.models import MerchantMeta

from .discounts import select_discount_percentage
from .shopify_client import ShopifyClient
from .forms import ShopifyOAuthSignupForm
from accounts.forms import CustomLoginForm
from accounts.models import CustomUser


# Session key that stores the randomly generated OAuth state token.
STATE_SESSION_KEY = "shopify_oauth_state"
EMBEDDED_SHOP_SESSION_KEY = "shopify_embedded_shop"
EMBEDDED_AUTHORIZED_SESSION_KEY = "shopify_embedded_validated"
PENDING_ONBOARD_SESSION_KEY = "shopify_pending_shop"


class ShopifySessionTokenError(Exception):
    """Raised when a Shopify session token (``id_token``) is invalid."""



def _normalise_shop_domain(domain: str) -> str:
    """Return a normalised Shopify shop domain."""

    if not domain:
        return ""
    domain = domain.strip().lower()
    if domain.startswith("https://") or domain.startswith("http://"):
        domain = domain.split("://", 1)[1]
    return domain.strip("/")


def _session_token_key(domain: str) -> str:
    """Return the session key used to store temporary Shopify tokens."""

    return f"shopify_install_token:{_normalise_shop_domain(domain)}"


def _resolve_shopify_access_token(
    request: HttpRequest, normalised_domain: str
) -> str:
    """Return the best known access token for the Shopify store."""

    meta = (
        MerchantMeta.objects.filter(shopify_store_domain__iexact=normalised_domain)
        .exclude(shopify_access_token="")
        .exclude(shopify_access_token__isnull=True)
        .first()
    )
    if meta and meta.shopify_access_token:
        return meta.shopify_access_token

    return request.session.get(_session_token_key(normalised_domain), "")


def _ensure_shopify_link(
    user: CustomUser, shop_domain: str, access_token: str, *, company_name: str = ""
) -> None:
    """Persist Shopify credentials on the merchant's metadata record."""

    normalised_domain = _normalise_shop_domain(shop_domain)
    existing = (
        MerchantMeta.objects.filter(shopify_store_domain__iexact=normalised_domain)
        .exclude(user=user)
        .first()
    )
    if existing:
        raise ValueError("This Shopify store is already connected to a different account.")

    meta, _ = MerchantMeta.objects.get_or_create(
        user=user,
        defaults={
            "company_name": company_name or "",
            "business_type": MerchantMeta.BusinessType.SHOPIFY,
        },
    )

    fields_to_update = ["shopify_access_token", "shopify_store_domain", "business_type"]

    if company_name and company_name.strip():
        meta.company_name = company_name.strip()
        fields_to_update.append("company_name")

    meta.shopify_access_token = access_token
    meta.shopify_store_domain = normalised_domain
    meta.business_type = MerchantMeta.BusinessType.SHOPIFY
    meta.save(update_fields=fields_to_update)

    if not user.is_merchant:
        user.is_merchant = True
        user.save(update_fields=["is_merchant"])


def _render_shopify_error(request: HttpRequest, message: str, *, status_code: int = 400):
    """Render an error page suitable for embedded Shopify requests."""

    return render(
        request,
        "shopify_app/oauth_connect_error.html",
        {"error": message},
        status=status_code,
    )


@require_http_methods(["GET", "POST"])
def embedded_app_home(request: HttpRequest):
    """Surface signup/login flows for merchants inside the Shopify admin."""

    if request.method == "GET":
        shop = request.GET.get("shop", "")
        if not shop:
            return _render_shopify_error(request, "Missing Shopify shop parameter.")

        if not _validate_shopify_hmac(request.GET):
            return HttpResponseBadRequest("Invalid Shopify HMAC signature.")

        normalised_shop = _normalise_shop_domain(shop)
        access_token = _resolve_shopify_access_token(request, normalised_shop)
        if not access_token:
            return _render_shopify_error(
                request,
                "We couldn't find an authorized Shopify installation for this store."
                " Please reinstall the app from Shopify to continue.",
            )

        request.session[EMBEDDED_SHOP_SESSION_KEY] = normalised_shop
        request.session[EMBEDDED_AUTHORIZED_SESSION_KEY] = True

        if request.user.is_authenticated:
            meta = getattr(request.user, "merchantmeta", None)
            if meta and _normalise_shop_domain(meta.shopify_store_domain) == normalised_shop:
                return redirect("merchant_dashboard")

        context = {
            "signup_form": ShopifyOAuthSignupForm(),
            "login_form": CustomLoginForm(request),
            "shop_domain": normalised_shop,
        }
        return render(request, "shopify_app/oauth_connect.html", context)

    # POST handling requires a previously validated GET request so that we
    # trust the HMAC and store domain stored in the session.
    if not request.session.get(EMBEDDED_AUTHORIZED_SESSION_KEY):
        return HttpResponseBadRequest("Shopify session not initialised.")

    shop_domain = request.session.get(EMBEDDED_SHOP_SESSION_KEY, "")
    if not shop_domain:
        return HttpResponseBadRequest("Missing Shopify session context.")

    normalised_shop = _normalise_shop_domain(shop_domain)
    access_token = _resolve_shopify_access_token(request, normalised_shop)
    if not access_token:
        return _render_shopify_error(
            request,
            "We couldn't find an authorized Shopify installation for this store."
            " Please reinstall the app from Shopify to continue.",
        )

    action = (request.POST.get("action") or "").strip().lower()
    if action not in {"signup", "login"}:
        return HttpResponseBadRequest("Unsupported action.")

    if action == "signup":
        signup_form = ShopifyOAuthSignupForm(request.POST)
        login_form = CustomLoginForm(request)
        if signup_form.is_valid():
            user = signup_form.save()
            try:
                _ensure_shopify_link(
                    user,
                    normalised_shop,
                    access_token,
                    company_name=signup_form.get_company_name(),
                )
            except ValueError as exc:
                signup_form.add_error(None, str(exc))
            else:
                auth_login(request, user)
                request.session.pop(_session_token_key(normalised_shop), None)
                return redirect("merchant_dashboard")
    else:
        signup_form = ShopifyOAuthSignupForm()
        login_form = CustomLoginForm(request, data=request.POST)
        if login_form.is_valid():
            user = login_form.get_user()
            company_name = ""
            meta = getattr(user, "merchantmeta", None)
            if meta:
                company_name = meta.company_name
            try:
                _ensure_shopify_link(
                    user,
                    normalised_shop,
                    access_token,
                    company_name=company_name,
                )
            except ValueError as exc:
                login_form.add_error(None, str(exc))
            else:
                auth_login(request, user)
                request.session.pop(_session_token_key(normalised_shop), None)
                return redirect("merchant_dashboard")

    context = {
        "signup_form": signup_form,
        "login_form": login_form,
        "shop_domain": normalised_shop,
    }
    return render(request, "shopify_app/oauth_connect.html", context, status=400)


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

    # Shopify can return either the legacy OAuth `code` parameters or the new
    # session-token (`id_token`) when the embedded app loads from the admin.
    id_token = (request.GET.get("id_token") or "").strip()

    if id_token and not request.GET.get("code"):
        return _handle_session_token_callback(request, id_token)

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
    normalised_shop = _normalise_shop_domain(shop)
    request.session[_session_token_key(normalised_shop)] = access_token

    meta = (
        MerchantMeta.objects.select_related("user")
        .filter(shopify_store_domain__iexact=normalised_shop)
        .first()
    )

    if meta:
        meta.shopify_access_token = access_token
        meta.shopify_store_domain = normalised_shop
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        update_fields = [
            "shopify_access_token",
            "shopify_store_domain",
            "business_type",
        ]
        meta.save(update_fields=update_fields)

        user = getattr(meta, "user", None)
        if user and not user.is_merchant:
            user.is_merchant = True
            user.save(update_fields=["is_merchant"])

    # Provide a simple confirmation payload to the merchant (or installer) so
    # they know the OAuth process completed successfully.
    return JsonResponse({"status": "ok", "shop": normalised_shop})


def _handle_session_token_callback(request: HttpRequest, id_token: str) -> HttpResponse:
    """Handle the session-token (`id_token`) callback for embedded apps."""

    try:
        shop_domain, _payload = _verify_shopify_session_token(id_token)
    except ShopifySessionTokenError as exc:
        return HttpResponseBadRequest(str(exc))

    normalised_shop = _normalise_shop_domain(shop_domain)
    access_token = _resolve_shopify_access_token(request, normalised_shop)

    if not access_token:
        request.session[PENDING_ONBOARD_SESSION_KEY] = normalised_shop
        return redirect(f"/onboard/?shop={normalised_shop}")

    merchant_meta = (
        MerchantMeta.objects.select_related("user")
        .filter(shopify_store_domain__iexact=normalised_shop)
        .first()
    )

    if not merchant_meta or not merchant_meta.user:
        request.session[PENDING_ONBOARD_SESSION_KEY] = normalised_shop
        return redirect(f"/onboard/?shop={normalised_shop}")

    user = merchant_meta.user
    if not getattr(user, "backend", None):
        backends = getattr(settings, "AUTHENTICATION_BACKENDS", [])
        user.backend = backends[0] if backends else "django.contrib.auth.backends.ModelBackend"

    auth_login(request, user)
    request.session[EMBEDDED_SHOP_SESSION_KEY] = normalised_shop
    request.session[EMBEDDED_AUTHORIZED_SESSION_KEY] = True
    request.session[PENDING_ONBOARD_SESSION_KEY] = ""

    return redirect("merchant_dashboard")


def _verify_shopify_session_token(id_token: str) -> Tuple[str, Dict[str, Any]]:
    """Validate and decode Shopify's session token."""

    if not id_token:
        raise ShopifySessionTokenError("Missing Shopify session token.")

    if not settings.SHOPIFY_API_SECRET or not settings.SHOPIFY_API_KEY:
        raise ShopifySessionTokenError("Shopify API credentials are not configured.")

    leeway_setting = getattr(settings, "SHOPIFY_SESSION_TOKEN_LEEWAY", 0)
    try:
        leeway = int(leeway_setting)
    except (TypeError, ValueError):
        leeway = 0
    else:
        leeway = max(0, leeway)

    try:
        payload = jwt.decode(
            id_token,
            settings.SHOPIFY_API_SECRET,
            algorithms=["HS256"],
            audience=settings.SHOPIFY_API_KEY,
            leeway=leeway,
        )
    except jwt.ImmatureSignatureError as exc:
        raise ShopifySessionTokenError("Shopify session token is not yet valid.") from exc
    except jwt.ExpiredSignatureError as exc:
        raise ShopifySessionTokenError("Shopify session token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise ShopifySessionTokenError("Invalid Shopify session token.") from exc

    destination = payload.get("dest") or payload.get("iss") or ""
    destination = destination.split("://", 1)[-1]
    shop_domain = _normalise_shop_domain(destination)
    if not shop_domain:
        raise ShopifySessionTokenError("Shopify session token did not include a shop domain.")

    issuer = payload.get("iss", "")
    if issuer and shop_domain not in issuer:
        raise ShopifySessionTokenError("Shopify session token issuer mismatch.")

    return shop_domain, payload


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
