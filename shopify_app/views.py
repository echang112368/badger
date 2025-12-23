"""Views for interacting with Shopify."""

import logging

import base64
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

import jwt
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from merchants.models import MerchantMeta

from . import billing
from .discounts import select_discount_percentage
from .forms import ShopifyOAuthSignupForm
from .oauth import (
    CALLBACK_SESSION_KEY,
    STATE_SESSION_KEY,
    ShopifyOAuthError,
    ShopifyOAuthService,
    normalise_shop_domain,
    session_refresh_key,
    session_scope_key,
    session_token_key,
    validate_shopify_hmac,
)
from .shopify_client import ShopifyClient, ShopifyGraphQLError, ShopifyInvalidCredentialsError
from .token_management import clear_shopify_token_for_shop, refresh_shopify_token
from accounts.forms import CustomLoginForm
from accounts.models import CustomUser


logger = logging.getLogger(__name__)


EMBEDDED_SHOP_SESSION_KEY = "shopify_embedded_shop"
EMBEDDED_AUTHORIZED_SESSION_KEY = "shopify_embedded_validated"
PENDING_ONBOARD_SESSION_KEY = "shopify_pending_shop"
SESSION_TOKEN_RETRY_PREFIX = "shopify_session_retry:"
SESSION_TOKEN_RETRY_COOLDOWN_SECONDS = 30
class ShopifySessionTokenError(Exception):
    """Raised when a Shopify session token (``id_token``) is invalid."""


def _resolve_shopify_access_token(
    request: HttpRequest, normalised_domain: str
) -> str:
    """Return the best known access token for the Shopify store."""

    session_token = request.session.get(session_token_key(normalised_domain), "")
    if session_token:
        logger.info(
            "Using Shopify access token from session for %s: %s",
            normalised_domain,
            session_token,
        )
        return session_token

    meta = (
        MerchantMeta.objects.filter(shopify_store_domain__iexact=normalised_domain)
        .exclude(shopify_access_token="")
        .exclude(shopify_access_token__isnull=True)
        .first()
    )
    if meta and meta.shopify_access_token:
        logger.info(
            "Using Shopify access token from MerchantMeta for %s: %s",
            normalised_domain,
            meta.shopify_access_token,
        )
        return meta.shopify_access_token

    fallback_token = request.session.get(session_token_key(normalised_domain), "")
    if fallback_token:
        logger.info(
            "Falling back to Shopify access token from session for %s: %s",
            normalised_domain,
            fallback_token,
        )
    else:
        logger.info(
            "No Shopify access token available for %s", normalised_domain
        )
    return fallback_token


def _resolve_shopify_refresh_token(
    request: HttpRequest, normalised_domain: str
) -> str:
    """Return the best known refresh token for the Shopify store."""

    session_token = request.session.get(session_refresh_key(normalised_domain), "")
    if session_token:
        logger.info(
            "Using Shopify refresh token from session for %s: %s",
            normalised_domain,
            session_token,
        )
        return session_token

    meta = (
        MerchantMeta.objects.filter(shopify_store_domain__iexact=normalised_domain)
        .exclude(shopify_refresh_token="")
        .exclude(shopify_refresh_token__isnull=True)
        .first()
    )
    if meta and getattr(meta, "shopify_refresh_token", ""):
        logger.info(
            "Using Shopify refresh token from MerchantMeta for %s: %s",
            normalised_domain,
            meta.shopify_refresh_token,
        )
        return meta.shopify_refresh_token

    fallback_refresh = request.session.get(session_refresh_key(normalised_domain), "")
    if fallback_refresh:
        logger.info(
            "Falling back to Shopify refresh token from session for %s: %s",
            normalised_domain,
            fallback_refresh,
        )
    else:
        logger.info(
            "No Shopify refresh token available for %s", normalised_domain
        )
    return fallback_refresh


def _ensure_shopify_link(
    request: HttpRequest,
    user: CustomUser,
    shop_domain: str,
    access_token: str,
    *,
    company_name: str = "",
    scope: str = "",
    refresh_token: str = "",
) -> MerchantMeta:
    """Persist Shopify credentials on the merchant's metadata record."""

    normalised_domain = normalise_shop_domain(shop_domain)
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
    if refresh_token:
        meta.shopify_refresh_token = refresh_token
        fields_to_update.append("shopify_refresh_token")

    if company_name and company_name.strip():
        meta.company_name = company_name.strip()
        fields_to_update.append("company_name")

    if scope:
        meta.shopify_oauth_authorization_line = _format_authorization_line(scope)
        fields_to_update.append("shopify_oauth_authorization_line")

    meta.shopify_access_token = access_token
    meta.shopify_store_domain = normalised_domain
    meta.business_type = MerchantMeta.BusinessType.SHOPIFY
    meta.save(update_fields=fields_to_update)

    if not user.is_merchant:
        user.is_merchant = True
        user.save(update_fields=["is_merchant"])

    return meta


def _format_authorization_line(scope: str) -> str:
    scope_value = ",".join(sorted(part.strip() for part in scope.split(",") if part.strip()))
    timestamp = timezone.now().isoformat()
    if scope_value:
        return f"scope={scope_value};connected_at={timestamp}"
    return f"connected_at={timestamp}"


def _render_shopify_error(request: HttpRequest, message: str, *, status_code: int = 400):
    """Render an error page suitable for embedded Shopify requests."""

    return render(
        request,
        "shopify_app/oauth_connect_error.html",
        {"error": message},
        status=status_code,
    )


def _session_retry_key(shop_domain: str) -> str:
    """Return the session key used to throttle retry attempts."""

    return f"{SESSION_TOKEN_RETRY_PREFIX}{shop_domain}"


def _clear_shopify_session_state(request: HttpRequest, shop_domain: str = "") -> None:
    """Remove cached Shopify session information for the current visitor."""

    keys_to_clear = [
        STATE_SESSION_KEY,
        CALLBACK_SESSION_KEY,
        EMBEDDED_SHOP_SESSION_KEY,
        EMBEDDED_AUTHORIZED_SESSION_KEY,
        PENDING_ONBOARD_SESSION_KEY,
    ]

    for key in keys_to_clear:
        request.session.pop(key, None)

    normalised_shop = normalise_shop_domain(shop_domain)
    if normalised_shop:
        request.session.pop(session_token_key(normalised_shop), None)
        request.session.pop(session_scope_key(normalised_shop), None)
        request.session.pop(session_refresh_key(normalised_shop), None)
        retry_key = _session_retry_key(normalised_shop)
        request.session.pop(retry_key, None)


def build_shopify_authorize_url(request: HttpRequest, shop_domain: str) -> str:
    """Return the Shopify OAuth installation URL for the specified store."""

    normalised_shop = normalise_shop_domain(shop_domain)
    service = ShopifyOAuthService(request)
    api_key = getattr(settings, "SHOPIFY_API_KEY", "")
    if api_key:
        try:
            return service.begin_installation(normalised_shop)
        except ShopifyOAuthError as exc:
            logger.error(
                "Failed to construct Shopify installation URL for %s: %s",
                normalised_shop,
                exc,
            )

    authorize_path = reverse("shopify_oauth_authorize")
    if normalised_shop:
        query = urlencode({"shop": normalised_shop})
        authorize_path = f"{authorize_path}?{query}"
    return request.build_absolute_uri(authorize_path)


def _build_reauthorization_payload(
    request: HttpRequest, shop_domain: str, *, message: str = ""
) -> dict:
    """Return a payload instructing the caller to restart Shopify OAuth."""

    normalised_shop = normalise_shop_domain(shop_domain)
    clear_shopify_token_for_shop(normalised_shop)
    authorize_url = build_shopify_authorize_url(request, normalised_shop)
    logger.warning(
        "Shopify access token for %s is invalid. Prompting reinstallation.",
        normalised_shop,
    )

    return {
        "error": message
        or "Shopify rejected the request because the stored credentials are invalid."
        " Please reinstall the Shopify app to continue.",
        "authorize_url": authorize_url,
        "shop_domain": normalised_shop,
    }


def _extract_shop_from_request(request: HttpRequest) -> str:
    """Return the best-effort shop domain found in the callback request."""

    shop_param = normalise_shop_domain(request.GET.get("shop", ""))
    if shop_param:
        return shop_param

    host_param = (request.GET.get("host") or "").strip()
    if host_param:
        padded = host_param + "=" * (-len(host_param) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        if decoded:
            # Embedded host strings typically look like
            # "admin.shopify.com/store/<store-slug>".
            parts = decoded.split("/store/", 1)
            if len(parts) == 2 and parts[1]:
                slug = parts[1].split("/", 1)[0]
                if slug:
                    return normalise_shop_domain(f"{slug}.myshopify.com")


def _extract_shop_from_id_token(id_token: str) -> str:
    """Parse the shop domain from a Shopify session token without verifying it."""

    token = (id_token or "").strip()
    if not token:
        return ""

    try:
        payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError:
        return ""

    destination = payload.get("dest") or payload.get("iss") or ""
    destination = destination.split("://", 1)[-1]
    return normalise_shop_domain(destination)

    return ""


@require_http_methods(["GET", "POST"])
def embedded_app_home(request: HttpRequest):
    """Surface signup/login flows for merchants inside the Shopify admin."""

    if request.method == "GET":
        shop = request.GET.get("shop", "")
        if not shop:
            return _render_shopify_error(request, "Missing Shopify shop parameter.")

        if not validate_shopify_hmac(request.GET):
            return HttpResponseBadRequest("Invalid Shopify HMAC signature.")

        normalised_shop = normalise_shop_domain(shop)
        access_token = _resolve_shopify_access_token(request, normalised_shop)
        if not access_token:
            return _render_shopify_error(
                request,
                "We couldn't find an authorized Shopify installation for this store. "
                "Please reinstall the app from Shopify to continue.",
            )

        request.session[EMBEDDED_SHOP_SESSION_KEY] = normalised_shop
        request.session[EMBEDDED_AUTHORIZED_SESSION_KEY] = True

        if request.user.is_authenticated:
            meta = getattr(request.user, "merchantmeta", None)
            if meta and normalise_shop_domain(meta.shopify_store_domain) == normalised_shop:
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

    normalised_shop = normalise_shop_domain(shop_domain)
    access_token = _resolve_shopify_access_token(request, normalised_shop)
    refresh_token = _resolve_shopify_refresh_token(request, normalised_shop)
    if not access_token:
        return _render_shopify_error(
            request,
            "We couldn't find an authorized Shopify installation for this store. "
            "Please reinstall the app from Shopify to continue.",
        )
    scope = request.session.get(session_scope_key(normalised_shop), "")

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
                    request,
                    user,
                    normalised_shop,
                    access_token,
                    company_name=signup_form.get_company_name(),
                    scope=scope,
                    refresh_token=refresh_token,
                )
            except ValueError as exc:
                signup_form.add_error(None, str(exc))
            else:
                auth_login(request, user)
                request.session.pop(session_token_key(normalised_shop), None)
                request.session.pop(session_scope_key(normalised_shop), None)
                request.session.pop(session_refresh_key(normalised_shop), None)
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
                    request,
                    user,
                    normalised_shop,
                    access_token,
                    company_name=company_name,
                    scope=scope,
                    refresh_token=refresh_token,
                )
            except ValueError as exc:
                login_form.add_error(None, str(exc))
            else:
                auth_login(request, user)
                request.session.pop(session_token_key(normalised_shop), None)
                request.session.pop(session_scope_key(normalised_shop), None)
                request.session.pop(session_refresh_key(normalised_shop), None)
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

    client = ShopifyClient(
        meta.shopify_access_token,
        meta.shopify_store_domain,
        refresh_handler=lambda: refresh_shopify_token(meta),
    )

    coupon_code = f"BADGER-{uuid.uuid4().hex[:8].upper()}"
    now = timezone.now()
    discount_payload = {
        "title": coupon_code,
        "code": coupon_code,
        "usageLimit": 1,
        "startsAt": now.isoformat(),
        "endsAt": (now + timezone.timedelta(days=1)).isoformat(),
        "combinesWith": {"orderDiscounts": True, "productDiscounts": True, "shippingDiscounts": True},
        "customerSelection": {"all": True},
        "customerGets": {
            "value": {"percentage": float(percentage)},
            "items": {"all": True},
        },
    }

    try:
        payload = client.graphql(
            _DISCOUNT_CODE_CREATE_MUTATION,
            {"basicCodeDiscount": discount_payload},
        )
        result = payload.get("data", {}).get("discountCodeBasicCreate") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            raise ShopifyGraphQLError("Shopify rejected the discount creation.", user_errors)
    except ShopifyInvalidCredentialsError:
        payload = _build_reauthorization_payload(
            request,
            meta.shopify_store_domain,
            message="Shopify rejected the discount request because the stored credentials are invalid.",
        )
        return Response(payload, status=status.HTTP_401_UNAUTHORIZED)

    return Response({"coupon_code": coupon_code, "discount": percentage})


_DISCOUNT_CODE_CREATE_MUTATION = """
mutation CreateBasicDiscount($basicCodeDiscount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
    codeDiscountNode {
      id
    }
    userErrors {
      field
      message
    }
  }
}
"""


@require_GET
def oauth_authorize(request: HttpRequest):
    """Begin the Shopify OAuth installation flow."""

    service = ShopifyOAuthService(request)
    shop = (request.GET.get("shop") or "").strip()

    try:
        authorization_url = service.begin_installation(shop)
    except ShopifyOAuthError as exc:
        message = str(exc)
        status_code = 400
        if "credentials" in message.lower():
            status_code = 500
        return JsonResponse({"error": message}, status=status_code)

    return TemplateResponse(
        request,
        "shopify_app/oauth_authorize_redirect.html",
        {"redirect_url": authorization_url},
    )


@require_GET
def oauth_callback(request: HttpRequest):
    """Handle Shopify's OAuth callback and persist the merchant token."""

    # Shopify can return either the legacy OAuth `code` parameters or the new
    # session-token (`id_token`) when the embedded app loads from the admin.
    id_token = (request.GET.get("id_token") or "").strip()

    if id_token and not request.GET.get("code"):
        return _handle_session_token_callback(request, id_token)

    service = ShopifyOAuthService(request)
    try:
        token_response = service.complete_installation(request.GET)
    except ShopifyOAuthError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    normalised_shop = normalise_shop_domain(request.GET.get("shop", ""))
    access_token = (token_response.access_token or "").strip()
    if not access_token:
        logger.error(
            "Shopify OAuth callback for %s did not include an access token.",
            normalised_shop,
        )
        return JsonResponse(
            {"error": "Shopify did not return an access token."}, status=400
        )

    logger.info(
        "Received Shopify OAuth tokens for %s: access_token=%s refresh_token=%s",
        normalised_shop,
        access_token,
        token_response.refresh_token,
    )

    meta = (
        MerchantMeta.objects.select_related("user")
        .filter(shopify_store_domain__iexact=normalised_shop)
        .first()
    )

    updated_via_authenticated_user = False
    if request.user.is_authenticated:
        company_name = ""
        existing_meta = getattr(request.user, "merchantmeta", None)
        if existing_meta:
            company_name = existing_meta.company_name
        try:
            meta = _ensure_shopify_link(
                request,
                request.user,
                normalised_shop,
                access_token,
                company_name=company_name,
                scope=token_response.scope,
                refresh_token=token_response.refresh_token,
            )
            updated_via_authenticated_user = True
        except ValueError as exc:
            if meta is None:
                return JsonResponse({"error": str(exc)}, status=400)

    if meta and not updated_via_authenticated_user:
        defaults = {
            "shopify_access_token": access_token,
            "shopify_store_domain": normalised_shop,
            "business_type": MerchantMeta.BusinessType.SHOPIFY,
        }
        if token_response.refresh_token:
            defaults["shopify_refresh_token"] = token_response.refresh_token
        if token_response.scope:
            defaults[
                "shopify_oauth_authorization_line"
            ] = _format_authorization_line(token_response.scope)

        MerchantMeta.objects.update_or_create(pk=meta.pk, defaults=defaults)
        meta.refresh_from_db()
        logger.info(
            "Persisted Shopify OAuth tokens for MerchantMeta %s (%s).",
            meta.pk,
            normalised_shop,
        )

        user = getattr(meta, "user", None)
        if user and not user.is_merchant:
            user.is_merchant = True
            user.save(update_fields=["is_merchant"])

    confirmation_url: Optional[str] = None

    app_key = getattr(settings, "SHOPIFY_API_KEY", "").strip()
    if not app_key:
        # This should not happen because the OAuth service guards against it,
        # but fall back to redirecting directly to the embedded app so that we
        # at least return the user to the onboarding screen.
        dashboard_url = request.build_absolute_uri(
            f"{reverse('shopify_embedded_home')}?{urlencode({'shop': normalised_shop})}"
        )
    else:
        dashboard_url = f"https://{normalised_shop}/admin/apps/{app_key}"

    redirect_url = confirmation_url or dashboard_url
    if not confirmation_url:
        logger.warning(
            "No Shopify billing confirmation URL for %s; redirecting to app dashboard.",
            normalised_shop,
        )
    request.session[EMBEDDED_SHOP_SESSION_KEY] = normalised_shop
    request.session[EMBEDDED_AUTHORIZED_SESSION_KEY] = True

    context = {
        "shop_domain": normalised_shop,
        "redirect_url": redirect_url,
        "scopes": token_response.scope,
    }
    return render(
        request,
        "shopify_app/oauth_callback_complete.html",
        context,
    )


@require_GET
def billing_return(request: HttpRequest) -> HttpResponse:
    """Redirect the merchant back to the app dashboard after billing."""

    shop = normalise_shop_domain(request.GET.get("shop", ""))
    if not shop:
        return HttpResponseBadRequest("Missing shop identifier.")

    meta = (
        MerchantMeta.objects.filter(shopify_store_domain__iexact=shop)
        .select_related("user")
        .first()
    )
    if not meta:
        return HttpResponseBadRequest("Unknown Shopify store.")

    app_key = getattr(settings, "SHOPIFY_API_KEY", "").strip()
    dashboard_url = request.build_absolute_uri(
        f"{reverse('shopify_embedded_home')}?{urlencode({'shop': shop})}"
    )
    if app_key:
        dashboard_url = f"https://{shop}/admin/apps/{app_key}"

    return redirect(dashboard_url)


def _handle_session_token_callback(request: HttpRequest, id_token: str) -> HttpResponse:
    """Handle the session-token (`id_token`) callback for embedded apps."""

    try:
        shop_domain, _payload = _verify_shopify_session_token(id_token)
    except ShopifySessionTokenError as exc:
        shop_from_request = _extract_shop_from_request(request)
        shop_from_token = _extract_shop_from_id_token(id_token)
        shop_hint = shop_from_request or shop_from_token
        if shop_hint:
            normalised_shop = normalise_shop_domain(shop_hint)
            retry_key = _session_retry_key(normalised_shop)
            now_ts = timezone.now().timestamp()
            last_retry_ts = 0.0
            try:
                last_retry_ts = float(request.session.get(retry_key, 0) or 0)
            except (TypeError, ValueError):
                last_retry_ts = 0.0

            if now_ts - last_retry_ts < SESSION_TOKEN_RETRY_COOLDOWN_SECONDS:
                return _render_shopify_error(
                    request,
                    "We couldn't validate your Shopify session. Please try again in a moment.",
                )

            _clear_shopify_session_state(request, normalised_shop)
            request.session[PENDING_ONBOARD_SESSION_KEY] = normalised_shop
            request.session[retry_key] = str(now_ts)
            authorize_url = build_shopify_authorize_url(request, normalised_shop)
            return redirect(authorize_url)

        return HttpResponseBadRequest(str(exc))

    normalised_shop = normalise_shop_domain(shop_domain)
    access_token = _resolve_shopify_access_token(request, normalised_shop)

    if not access_token:
        _clear_shopify_session_state(request, normalised_shop)
        request.session[PENDING_ONBOARD_SESSION_KEY] = normalised_shop
        authorize_url = build_shopify_authorize_url(request, normalised_shop)
        return redirect(authorize_url)

    merchant_meta = (
        MerchantMeta.objects.select_related("user")
        .filter(shopify_store_domain__iexact=normalised_shop)
        .first()
    )

    if not merchant_meta or not merchant_meta.user:
        _clear_shopify_session_state(request, normalised_shop)
        request.session[PENDING_ONBOARD_SESSION_KEY] = normalised_shop
        authorize_url = build_shopify_authorize_url(request, normalised_shop)
        return redirect(authorize_url)

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

    try:
        payload = jwt.decode(
            id_token,
            settings.SHOPIFY_API_SECRET,
            algorithms=["HS256"],
            audience=settings.SHOPIFY_API_KEY,
        )
    except jwt.ExpiredSignatureError as exc:
        raise ShopifySessionTokenError("Shopify session token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise ShopifySessionTokenError("Invalid Shopify session token.") from exc

    destination = payload.get("dest") or payload.get("iss") or ""
    destination = destination.split("://", 1)[-1]
    shop_domain = normalise_shop_domain(destination)
    if not shop_domain:
        raise ShopifySessionTokenError("Shopify session token did not include a shop domain.")

    issuer = payload.get("iss", "")
    if issuer and shop_domain not in issuer:
        raise ShopifySessionTokenError("Shopify session token issuer mismatch.")

    return shop_domain, payload
