"""Webhook verification and dispatch for Shopify Admin webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Dict

from django.conf import settings

from .shopify_client import ShopifyGraphQLError, ShopifyClient

logger = logging.getLogger(__name__)


def _verify_hmac(raw_body: bytes, provided_hmac: str) -> bool:
    secret = getattr(settings, "SHOPIFY_API_SECRET", "")
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, provided_hmac or "")


def verify_webhook(request) -> Dict[str, Any]:
    raw_body = request.body or b""
    topic = request.headers.get("X-Shopify-Topic", "")
    shop = request.headers.get("X-Shopify-Shop-Domain", "")
    provided_hmac = request.headers.get("X-Shopify-Hmac-Sha256", "")

    if not _verify_hmac(raw_body, provided_hmac):
        logger.warning("WEBHOOK_REJECTED", extra={"reason": "invalid_hmac", "shop": shop})
        raise ShopifyGraphQLError("Invalid webhook signature")

    logger.info("WEBHOOK_VERIFIED", extra={"topic": topic, "shop": shop})
    payload = json.loads(raw_body.decode() or "{}")
    return {"topic": topic, "shop": shop, "payload": payload}


def register_webhook(shop_domain: str, access_token: str, topic: str, callback_url: str):
    mutation = """
    mutation CreateWebhook($topic: WebhookSubscriptionTopic!, $callbackUrl: URL!) {
      webhookSubscriptionCreate(
        topic: $topic
        webhookSubscription: {callbackUrl: $callbackUrl, format: JSON}
      ) {
        webhookSubscription { id topic callbackUrl }
        userErrors { field message }
      }
    }
    """

    client = ShopifyClient(access_token, shop_domain)
    payload = client.graphql(mutation, {"topic": topic, "callbackUrl": callback_url})
    data = payload.get("data", {}).get("webhookSubscriptionCreate") or {}
    if data.get("userErrors"):
        raise ShopifyGraphQLError(str(data.get("userErrors")))
    if not data.get("webhookSubscription"):
        raise ShopifyGraphQLError("Shopify webhook creation did not return a subscription.")
    return data.get("webhookSubscription")
