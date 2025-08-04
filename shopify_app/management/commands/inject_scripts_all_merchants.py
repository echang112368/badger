from django.core.management.base import BaseCommand

from merchants.models import MerchantMeta
from shopify_app.shopify_client import ShopifyClient

SCRIPT_SRC = "https://YOUR_NGROK_OR_DOMAIN/static/js/referral_tracker.js"


class Command(BaseCommand):
    help = "Inject referral tracking script into all merchants' Shopify stores"

    def handle(self, *args, **options):
        for merchant in MerchantMeta.objects.all():
            api_key = merchant.shopify_api_key
            password = merchant.shopify_api_password
            store_domain = getattr(merchant, "shopify_store_domain", None)

            if not (api_key and password and store_domain):
                self.stdout.write(
                    f"Skipping merchant {merchant.id}: missing Shopify credentials"
                )
                continue

            client = ShopifyClient(api_key, password, store_domain)
            try:
                existing = client.get("/admin/api/2023-07/script_tags.json")
                tags = existing.json().get("script_tags", [])
                if any(tag.get("src") == SCRIPT_SRC for tag in tags):
                    self.stdout.write(
                        f"Script already present for {store_domain}, skipping"
                    )
                    continue

                payload = {
                    "script_tag": {
                        "event": "onload",
                        "src": SCRIPT_SRC,
                    }
                }
                client.post("/admin/api/2023-07/script_tags.json", json=payload)
                self.stdout.write(f"Injected script for {store_domain}")
            except Exception as exc:
                self.stderr.write(
                    f"Failed to inject script for {store_domain}: {exc}"
                )
