import json
import requests


def register_orders_create_webhook(shop_domain: str, access_token: str,
                                    webhook_url: str = "https://42063a3c7da8.ngrok-free.app/shopify/webhooks/orders-create/"):
    """Register an orders/create webhook for the given shop."""
    url = f"https://{shop_domain}/admin/api/2024-07/webhooks.json"
    payload = {
        "webhook": {
            "topic": "orders/create",
            "address": webhook_url,
            "format": "json"
        }
    }
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        print(response.status_code)
        print(response.text)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Error registering webhook: {exc}")
        if hasattr(exc, 'response') and exc.response is not None:
            print(exc.response.status_code)
            print(exc.response.text)
