import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from shopify_app.oauth import normalise_shop_domain
from shopify_app.webhook_verification import is_valid_shopify_webhook


logger = logging.getLogger(__name__)


def _extract_shop_domain(request, payload: dict) -> str:
    shop_domain = request.headers.get("X-Shopify-Shop-Domain") or payload.get("shop_domain")
    return normalise_shop_domain(shop_domain)


@csrf_exempt
@require_POST
def customers_data_request_webhook(request):
    if not is_valid_shopify_webhook(request):
        return JsonResponse({"error": "Invalid Shopify webhook signature."}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    shop_domain = _extract_shop_domain(request, payload)
    logger.info("Received Shopify customers/data_request for %s.", shop_domain or "unknown shop")
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def customers_redact_webhook(request):
    if not is_valid_shopify_webhook(request):
        return JsonResponse({"error": "Invalid Shopify webhook signature."}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    shop_domain = _extract_shop_domain(request, payload)
    logger.info("Received Shopify customers/redact for %s.", shop_domain or "unknown shop")
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def shop_redact_webhook(request):
    if not is_valid_shopify_webhook(request):
        return JsonResponse({"error": "Invalid Shopify webhook signature."}, status=401)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    shop_domain = _extract_shop_domain(request, payload)
    logger.info("Received Shopify shop/redact for %s.", shop_domain or "unknown shop")
    return JsonResponse({"status": "ok"})
