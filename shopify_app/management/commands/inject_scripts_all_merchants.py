from django.core.management.base import BaseCommand

from merchants.models import MerchantMeta
from shopify_app.script_tags import SCRIPT_SRCS, ensure_script_tags
from shopify_app.shopify_client import ShopifyClient
from shopify_app.token_management import refresh_shopify_token


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
                token_type="offline",
            )
            try:
                injected, existing = ensure_script_tags(client)
                for src in SCRIPT_SRCS:
                    if src in existing:
                        self.stdout.write(
                            f"Script {src} already present for {store_domain}, skipping"
                        )
                        continue
                    if src in injected:
                        self.stdout.write(f"Injected script {src} for {store_domain}")
            except Exception as exc:
                self.stderr.write(
                    f"Failed to inject script for {store_domain}: {exc}"
                )
