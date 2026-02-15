import base64
import hashlib
import hmac

from django.conf import settings


def _candidate_webhook_secrets() -> list[str]:
    """Return Shopify webhook secrets, supporting key rotation and legacy names."""

    candidates = [
        getattr(settings, "SHOPIFY_API_SECRET", ""),
        getattr(settings, "SHOPIFY_API_SECRET_KEY", ""),
        getattr(settings, "SHOPIFY_CLIENT_SECRET", ""),
    ]
    configured = getattr(settings, "SHOPIFY_WEBHOOK_SECRETS", "")
    if configured:
        candidates.extend(str(configured).split(","))

    seen = set()
    secrets = []
    for value in candidates:
        secret = str(value or "").strip()
        if not secret or secret in seen:
            continue
        seen.add(secret)
        secrets.append(secret)
    return secrets


def is_valid_shopify_webhook(request) -> bool:
    provided_hmac = request.headers.get("X-Shopify-Hmac-Sha256") or request.META.get(
        "HTTP_X_SHOPIFY_HMAC_SHA256", ""
    )
    provided_hmac = str(provided_hmac or "").strip()
    secrets = _candidate_webhook_secrets()

    if not secrets or not provided_hmac:
        return False

    for secret in secrets:
        digest = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).digest()
        computed = base64.b64encode(digest).decode("utf-8")
        if hmac.compare_digest(computed, provided_hmac):
            return True
    return False
