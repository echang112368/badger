from shopify_app.shopify_client import ShopifyClient, ShopifyGraphQLError


WEBHOOK_CREATE_MUTATION = """
mutation CreateWebhook($topic: WebhookSubscriptionTopic!, $callbackUrl: URL!) {
  webhookSubscriptionCreate(
    topic: $topic
    webhookSubscription: {callbackUrl: $callbackUrl, format: JSON}
  ) {
    webhookSubscription {
      id
      topic
      callbackUrl
    }
    userErrors {
      field
      message
    }
  }
}
"""

def _register_webhook(
    *,
    shop_domain: str,
    access_token: str,
    topic: str,
    webhook_url: str,
) -> bool:
    client = ShopifyClient(access_token, shop_domain, token_type="offline")
    print(
        "Registering Shopify webhook:",
        {
            "shop_domain": shop_domain,
            "webhook_url": webhook_url,
            "topic": topic,
        },
    )
    try:
        payload = client.graphql(
            WEBHOOK_CREATE_MUTATION,
            {"topic": topic, "callbackUrl": webhook_url},
        )
    except Exception as exc:  # pragma: no cover - network errors
        print(f"Error registering webhook: {exc}")
        return False

    result = payload.get("data", {}).get("webhookSubscriptionCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        print(
            "Shopify webhook user errors:",
            {
                "shop_domain": shop_domain,
                "webhook_url": webhook_url,
                "user_errors": user_errors,
            },
        )
        print(f"Error registering webhook: {user_errors}")
        return False

    subscription = result.get("webhookSubscription")
    if not subscription:
        print(
            "Shopify webhook creation failed to return subscription:",
            {
                "shop_domain": shop_domain,
                "webhook_url": webhook_url,
                "payload": payload,
            },
        )
        raise ShopifyGraphQLError(
            "Shopify webhook creation did not return a subscription.", payload
        )

    print(
        "Shopify webhook registered:",
        {
            "shop_domain": shop_domain,
            "webhook_url": webhook_url,
            "subscription": subscription,
        },
    )
    return True


def register_orders_create_webhook(
    shop_domain: str,
    access_token: str,
    webhook_url: str = "https://6457c6b55211.ngrok-free.app/shopify/webhooks/orders-create/",
):
    """Register an orders/create webhook for the given shop."""

    return _register_webhook(
        shop_domain=shop_domain,
        access_token=access_token,
        topic="ORDERS_CREATE",
        webhook_url=webhook_url,
    )


def register_app_uninstalled_webhook(
    shop_domain: str,
    access_token: str,
    webhook_url: str,
) -> bool:
    """Register an app/uninstalled webhook for the given shop."""

    return _register_webhook(
        shop_domain=shop_domain,
        access_token=access_token,
        topic="APP_UNINSTALLED",
        webhook_url=webhook_url,
    )


def register_customers_data_request_webhook(
    shop_domain: str,
    access_token: str,
    webhook_url: str,
) -> bool:
    """Register a customers/data_request GDPR webhook for the given shop."""

    return _register_webhook(
        shop_domain=shop_domain,
        access_token=access_token,
        topic="CUSTOMERS_DATA_REQUEST",
        webhook_url=webhook_url,
    )


def register_customers_redact_webhook(
    shop_domain: str,
    access_token: str,
    webhook_url: str,
) -> bool:
    """Register a customers/redact GDPR webhook for the given shop."""

    return _register_webhook(
        shop_domain=shop_domain,
        access_token=access_token,
        topic="CUSTOMERS_REDACT",
        webhook_url=webhook_url,
    )


def register_shop_redact_webhook(
    shop_domain: str,
    access_token: str,
    webhook_url: str,
) -> bool:
    """Register a shop/redact GDPR webhook for the given shop."""

    return _register_webhook(
        shop_domain=shop_domain,
        access_token=access_token,
        topic="SHOP_REDACT",
        webhook_url=webhook_url,
    )
