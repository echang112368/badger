import base64
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from merchants.models import MerchantMeta

from shopify_app.oauth import normalise_shop_domain
from shopify_app.token_management import clear_shopify_token_for_shop


logger = logging.getLogger(__name__)


def _is_valid_shopify_webhook(request) -> bool:
    secret = getattr(settings, "SHOPIFY_API_SECRET", "")
    provided_hmac = request.headers.get("X-Shopify-Hmac-Sha256") or request.META.get(
        "HTTP_X_SHOPIFY_HMAC_SHA256", ""
    )
    if not secret or not provided_hmac:
        return False

    digest = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, provided_hmac)


@csrf_exempt
@require_POST
def app_uninstall_webhook(request):
    if not _is_valid_shopify_webhook(request):
        return JsonResponse({"error": "Invalid Shopify webhook signature."}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    shop_domain = request.headers.get("X-Shopify-Shop-Domain") or payload.get("myshopify_domain")
    normalised = normalise_shop_domain(shop_domain)
    if not normalised:
        return JsonResponse({"error": "Missing shop domain."}, status=400)

    meta = clear_shopify_token_for_shop(normalised)
    if not meta:
        meta = (
            MerchantMeta.objects.filter(shopify_store_domain__iexact=normalised).first()
        )

    if meta:
        meta.cancel_shopify_account(canceled_at=timezone.now())
        logger.info("Marked Shopify merchant %s as cancelled.", normalised)
    else:
        logger.warning("Received uninstall webhook for unknown shop %s.", normalised)

    return JsonResponse({"status": "ok"})
