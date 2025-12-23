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


def register_orders_create_webhook(
    shop_domain: str,
    access_token: str,
    webhook_url: str = "https://50f494970026.ngrok-free.app/shopify/webhooks/orders-create/",
):
    """Register an orders/create webhook for the given shop."""

    client = ShopifyClient(access_token, shop_domain)
    try:
        payload = client.graphql(
            WEBHOOK_CREATE_MUTATION,
            {"topic": "ORDERS_CREATE", "callbackUrl": webhook_url},
        )
    except Exception as exc:  # pragma: no cover - network errors
        print(f"Error registering webhook: {exc}")
        return False

    result = payload.get("data", {}).get("webhookSubscriptionCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        print(f"Error registering webhook: {user_errors}")
        return False

    subscription = result.get("webhookSubscription")
    if not subscription:
        raise ShopifyGraphQLError(
            "Shopify webhook creation did not return a subscription.", payload
        )

    return True
