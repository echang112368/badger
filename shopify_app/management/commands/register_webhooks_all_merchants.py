from django.core.management.base import BaseCommand

from merchants.models import MerchantMeta
from shopify_app.webhooks import register_orders_create_webhook


class Command(BaseCommand):
    help = "Register Shopify orders/create webhooks for all merchants"

    def add_arguments(self, parser):
        parser.add_argument(
            "webhook_url",
            help="Public URL Shopify should call for orders/create events",
        )

    def handle(self, *args, **options):
        webhook_url = options["webhook_url"]
        for merchant in MerchantMeta.objects.all():
            access_token = merchant.shopify_access_token
            store_domain = getattr(merchant, "shopify_store_domain", None)

            if not (access_token and store_domain):
                self.stdout.write(
                    f"Skipping merchant {merchant.id}: missing Shopify credentials"
                )
                continue

            success = register_orders_create_webhook(
                store_domain, access_token, webhook_url
            )
            if success:
                self.stdout.write(f"Registered webhook for {store_domain}")
            else:
                self.stderr.write(
                    f"Failed to register webhook for {store_domain}"
                )
