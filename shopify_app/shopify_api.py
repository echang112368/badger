"""Utility functions for interacting with Shopify APIs."""
import requests
from django.conf import settings
from django.urls import reverse

API_SCOPES = ["read_orders", "read_script_tags", "write_script_tags"]


def post_collect_webhook(creator_uuid: str, merchant_uuid: str, order_id: str, total_price: str, currency: str, timestamp: str) -> None:
    """Send a Stripe-style webhook payload to the /collect/ endpoint."""
    try:
        amount_cents = int(float(total_price) * 100)
    except (TypeError, ValueError):
        amount_cents = 0

    payload = {
        "data": {
            "object": {
                "id": order_id,
                "amount": amount_cents,
                "currency": currency,
                "created": timestamp,
                "metadata": {
                    "uuid": creator_uuid,
                    "buisID": merchant_uuid,
                },
            }
        }
    }

    base_url = getattr(settings, "COLLECT_WEBHOOK_URL", None)
    if not base_url:
        base_url = "http://localhost:8000" + reverse("webhook_view")

    try:
        requests.post(base_url, json=payload, timeout=5)
    except Exception:
        # Fail silently to avoid disrupting order processing
        pass


def create_script_tag(shop_domain: str, api_key: str, password: str, script_url: str):
    """Register a ScriptTag in Shopify to load a script from script_url."""
    url = f"https://{api_key}:{password}@{shop_domain}/admin/api/2023-01/script_tags.json"
    data = {"script_tag": {"event": "onload", "src": script_url}}
    return requests.post(url, json=data)
