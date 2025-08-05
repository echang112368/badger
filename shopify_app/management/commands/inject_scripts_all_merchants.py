from django.core.management.base import BaseCommand

from merchants.models import MerchantMeta
from shopify_app.shopify_client import ShopifyClient

SCRIPT_SRC_BASE = "https://d9c1fdbdb8b1.ngrok-free.app/static/js/referral_tracker.js"


class Command(BaseCommand):
    help = "Inject referral tracking script into all merchants' Shopify stores"

    def handle(self, *args, **options):
        for merchant in MerchantMeta.objects.all():
            access_token = merchant.shopify_access_token
            store_domain = getattr(merchant, "shopify_store_domain", None)

            if not (access_token and store_domain):
                self.stdout.write(
                    f"Skipping merchant {merchant.id}: missing Shopify credentials"
                )
                continue

            client = ShopifyClient(access_token, store_domain)
            try:
                existing = client.get("/admin/api/2023-07/script_tags.json")
                tags = existing.get("script_tags", [])
                if any(tag.get("src", "").startswith(SCRIPT_SRC_BASE) for tag in tags):
                    self.stdout.write(
                        f"Script already present for {store_domain}, skipping"
                    )
                    continue

                payload = {
                    "script_tag": {
                        "event": "onload",
                        "src": f"{SCRIPT_SRC_BASE}?merchant_uuid={merchant.uuid}",
                    }
                }
                client.post("/admin/api/2023-07/script_tags.json", json=payload)
                self.stdout.write(f"Injected script for {store_domain}")
            except Exception as exc:
                self.stderr.write(
                    f"Failed to inject script for {store_domain}: {exc}"
                )
