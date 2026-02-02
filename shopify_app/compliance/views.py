import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from shopify_app.webhook_verification import is_valid_shopify_webhook


logger = logging.getLogger(__name__)


def _validate_shopify_webhook(request):
    if not is_valid_shopify_webhook(request):
        return JsonResponse({"error": "Invalid Shopify webhook signature."}, status=401)
    return None


def _parse_payload(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "Invalid JSON payload."}, status=400)


@csrf_exempt
@require_POST
def customers_data_request_webhook(request):
    invalid = _validate_shopify_webhook(request)
    if invalid:
        return invalid

    payload, error = _parse_payload(request)
    if error:
        return error

    logger.info(
        "Received Shopify customers/data_request webhook.",
        extra={"shop_domain": payload.get("shop_domain")},
    )
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def customers_redact_webhook(request):
    invalid = _validate_shopify_webhook(request)
    if invalid:
        return invalid

    payload, error = _parse_payload(request)
    if error:
        return error

    logger.info(
        "Received Shopify customers/redact webhook.",
        extra={"shop_domain": payload.get("shop_domain")},
    )
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def shop_redact_webhook(request):
    invalid = _validate_shopify_webhook(request)
    if invalid:
        return invalid

    payload, error = _parse_payload(request)
    if error:
        return error

    logger.info(
        "Received Shopify shop/redact webhook.",
        extra={"shop_domain": payload.get("shop_domain")},
    )
    return JsonResponse({"status": "ok"})
