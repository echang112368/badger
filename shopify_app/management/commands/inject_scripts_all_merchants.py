from django.core.management.base import BaseCommand

from merchants.models import MerchantMeta
from shopify_app.shopify_client import ShopifyClient, ShopifyGraphQLError
from shopify_app.token_management import refresh_shopify_token

SCRIPT_SRCS = [
    "https://50f494970026.ngrok-free.app/static/js/referral_tracker.js",
    "https://50f494970026.ngrok-free.app/static/js/cart_attributes.js",
    
]


class Command(BaseCommand):
    help = "Inject tracking scripts into all merchants' Shopify stores"

    def handle(self, *args, **options):
        for merchant in MerchantMeta.objects.all():
            access_token = merchant.shopify_access_token
            store_domain = getattr(merchant, "shopify_store_domain", None)

            if not (access_token and store_domain):
                self.stdout.write(
                    f"Skipping merchant {merchant.id}: missing Shopify credentials"
                )
                continue

            client = ShopifyClient(
                access_token,
                store_domain,
                refresh_handler=lambda m=merchant: refresh_shopify_token(m),
            )
            try:
                tags = _fetch_script_tags(client)
                for src in SCRIPT_SRCS:
                    if any(tag.get("src") == src for tag in tags):
                        self.stdout.write(
                            f"Script {src} already present for {store_domain}, skipping"
                        )
                        continue

                    _create_script_tag(client, src)
                    self.stdout.write(f"Injected script {src} for {store_domain}")
            except Exception as exc:
                self.stderr.write(
                    f"Failed to inject script for {store_domain}: {exc}"
                )


SCRIPT_TAGS_QUERY = """
query ScriptTags {
  scriptTags(first: 100) {
    edges {
      node {
        id
        src
        displayScope
      }
    }
  }
}
"""


SCRIPT_TAG_CREATE_MUTATION = """
mutation CreateScriptTag($src: URL!) {
  scriptTagCreate(input: {src: $src, displayScope: ONLINE_STORE}) {
    scriptTag {
      id
      src
      displayScope
    }
    userErrors {
      field
      message
    }
  }
}
"""


def _fetch_script_tags(client: ShopifyClient):
    payload = client.graphql(SCRIPT_TAGS_QUERY)
    edges = (payload.get("data", {}).get("scriptTags", {}).get("edges") or [])
    return [edge.get("node") or {} for edge in edges]


def _create_script_tag(client: ShopifyClient, src: str) -> None:
    payload = client.graphql(SCRIPT_TAG_CREATE_MUTATION, {"src": src})
    result = payload.get("data", {}).get("scriptTagCreate") or {}
    errors = result.get("userErrors") or []
    if errors:
        raise ShopifyGraphQLError("Failed to create script tag.", errors)
