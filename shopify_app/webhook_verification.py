import base64
import hashlib
import hmac

from django.conf import settings


def is_valid_shopify_webhook(request) -> bool:
    secret = getattr(settings, "SHOPIFY_API_SECRET", "")
    provided_hmac = request.headers.get("X-Shopify-Hmac-Sha256") or request.META.get(
        "HTTP_X_SHOPIFY_HMAC_SHA256", ""
    )
    if not secret or not provided_hmac:
        return False

    digest = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, provided_hmac)
