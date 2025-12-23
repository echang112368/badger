import base64
import json
import logging
import uuid
from datetime import datetime, timedelta

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.test import RequestFactory
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from accounts.models import CustomUser
from merchants.models import MerchantMeta

from .oauth import ShopifyOAuthError, ShopifyOAuthService, normalise_shop_domain, validate_shopify_hmac
from .shopify_client import ShopifyGraphQLError, graphql
from .webhooks import verify_webhook

logger = logging.getLogger(__name__)


DISCOUNT_MUTATION = """
mutation discountAutomaticAppCreate($automaticAppDiscount: DiscountAutomaticAppInput!) {
  discountAutomaticAppCreate(automaticAppDiscount: $automaticAppDiscount) {
    automaticAppDiscount { id title status }
    userErrors { field message }
  }
}
"""


def build_shopify_authorize_url(shop: str) -> str:
    """Legacy helper preserved for compatibility with merchant views."""

    service = ShopifyOAuthService(RequestFactory().get("/"))
    return service.begin_installation(shop)


@require_GET
def embedded_app_home(request: HttpRequest):
    """Entry point for the embedded app; expects session token middleware."""
    shop = getattr(request, "shop_domain", "")
    if not shop:
        return JsonResponse({"error": "unauthorized"}, status=401)

    logger.info(
        "EMBEDDED_APP_LOAD",
        extra={"shop": shop, "request_id": getattr(request, "request_id", "")},
    )
    return JsonResponse({"shop": shop, "status": "ok"})


@require_GET
def install(request: HttpRequest):
    shop = request.GET.get("shop", "")
    service = ShopifyOAuthService(request)
    redirect_url = service.begin_installation(shop)
    return redirect(redirect_url)


@require_GET
def oauth_callback(request: HttpRequest):
    service = ShopifyOAuthService(request)
    token_response = service.complete_installation(request.GET)
    shop = normalise_shop_domain(request.GET.get("shop", ""))
    user, _ = CustomUser.objects.get_or_create(username=shop, defaults={"email": f"{shop}@example.com"})
    meta, _ = MerchantMeta.objects.get_or_create(user=user)
    meta.shopify_store_domain = shop
    meta.shopify_access_token = token_response.access_token
    meta.shopify_refresh_token = token_response.refresh_token
    meta.shopify_oauth_authorization_line = token_response.scope
    meta.business_type = MerchantMeta.BusinessType.SHOPIFY
    meta.save()

    logger.info("OAUTH_SUCCESS", extra={"shop": shop, "scopes": token_response.scope})
    return redirect(reverse("shopify_embedded_home"))


@csrf_exempt
@require_POST
def webhook_receiver(request: HttpRequest):
    event = verify_webhook(request)
    return JsonResponse({"received": event["topic"], "shop": event["shop"]})


@require_POST
def create_discount(request: HttpRequest, merchant_uuid: uuid.UUID):
    try:
        meta = MerchantMeta.objects.get(uuid=merchant_uuid)
    except MerchantMeta.DoesNotExist:
        return JsonResponse({"error": "merchant_not_found"}, status=404)

    if not meta.shopify_access_token or not meta.shopify_store_domain:
        return JsonResponse({"error": "missing_credentials"}, status=400)

    now = datetime.utcnow()
    variables = {
        "automaticAppDiscount": {
            "title": f"Badger-{now.strftime('%Y%m%d%H%M%S')}",
            "startsAt": now.isoformat() + "Z",
            "endsAt": (now + timedelta(days=1)).isoformat() + "Z",
            "customerGets": {"value": {"percentage": 10}, "items": {"all": True}},
            "appliesOncePerCustomer": True,
        }
    }

    try:
        payload = graphql(meta.shopify_store_domain, meta.shopify_access_token, DISCOUNT_MUTATION, variables)
    except ShopifyGraphQLError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    user_errors = payload.get("data", {}).get("discountAutomaticAppCreate", {}).get("userErrors") or []
    if user_errors:
        return JsonResponse({"error": user_errors}, status=400)

    return JsonResponse({"status": "SUCCESS", "discount": variables["automaticAppDiscount"]})


@require_GET
def billing_return(request: HttpRequest):
    shop = normalise_shop_domain(request.GET.get("shop", ""))
    charge_id = request.GET.get("charge_id", "")
    if not validate_shopify_hmac(request.GET):
        return JsonResponse({"error": "invalid_hmac"}, status=401)

    try:
        meta = MerchantMeta.objects.get(shopify_store_domain=shop)
    except MerchantMeta.DoesNotExist:
        return JsonResponse({"error": "merchant_not_found"}, status=404)

    query = """
    query getSubscription($id: ID!) {
      appSubscription(id: $id) { id status }
    }
    """
    payload = graphql(meta.shopify_store_domain, meta.shopify_access_token, query, {"id": charge_id})
    subscription = payload.get("data", {}).get("appSubscription") or {}
    assert subscription.get("status") == "ACTIVE"
    meta.shopify_billing_status = subscription.get("status", "")
    meta.shopify_recurring_charge_id = charge_id
    meta.save(update_fields=["shopify_billing_status", "shopify_recurring_charge_id"])

    logger.info(
        "BILLING_CONFIRMED", extra={"shop": shop, "request_id": getattr(request, "request_id", "")}
    )
    return JsonResponse({"status": "active"})
