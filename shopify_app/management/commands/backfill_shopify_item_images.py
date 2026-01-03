import logging
from collections import defaultdict
from typing import Iterable, List

from django.core.management.base import BaseCommand

from merchants.models import MerchantItem, MerchantMeta
from shopify_app.shopify_client import ShopifyClient
from shopify_app.token_management import refresh_shopify_token


logger = logging.getLogger(__name__)


def _chunked(values: Iterable[str], size: int) -> Iterable[List[str]]:
    batch: List[str] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class Command(BaseCommand):
    help = "Backfill missing Shopify image URLs for merchant items."

    def add_arguments(self, parser):
        parser.add_argument(
            "--merchant-id",
            type=int,
            default=None,
            help="Only backfill items for a specific merchant ID.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of Shopify product IDs to request per batch.",
        )

    def handle(self, *args, **options):
        merchant_id = options.get("merchant_id")
        batch_size = options.get("batch_size") or 50

        items_qs = MerchantItem.objects.filter(
            shopify_product_id__isnull=False,
        ).exclude(shopify_product_id="").filter(
            image_url__isnull=True,
        )
        if merchant_id:
            items_qs = items_qs.filter(merchant_id=merchant_id)

        items_by_merchant = defaultdict(list)
        for item in items_qs:
            items_by_merchant[item.merchant_id].append(item)

        if not items_by_merchant:
            self.stdout.write(self.style.WARNING("No merchant items need backfill."))
            return

        updated_total = 0
        skipped_total = 0

        for merchant_id, items in items_by_merchant.items():
            meta = MerchantMeta.objects.filter(user_id=merchant_id).first()
            if not meta or not meta.shopify_access_token or not meta.shopify_store_domain:
                skipped_total += len(items)
                message = (
                    f"Skipping merchant_id={merchant_id}: missing Shopify credentials."
                )
                logger.info(message)
                self.stdout.write(self.style.WARNING(message))
                continue

            client = ShopifyClient(
                meta.shopify_access_token,
                meta.shopify_store_domain,
                refresh_handler=lambda: refresh_shopify_token(meta),
                token_type="offline",
            )

            product_ids = [item.shopify_product_id for item in items]
            for batch in _chunked(product_ids, batch_size):
                try:
                    products = client.get_products_by_ids(batch)
                except Exception:
                    logger.exception(
                        "Failed Shopify image backfill for merchant_id=%s batch=%s",
                        merchant_id,
                        batch,
                    )
                    continue

                products_by_id = {str(product.get("id")): product for product in products}
                for item in items:
                    if item.shopify_product_id not in batch:
                        continue
                    product = products_by_id.get(str(item.shopify_product_id), {})
                    featured_image = (
                        (product or {}).get("featuredImage") or {}
                    ).get("src")
                    if not featured_image:
                        images = (product or {}).get("images") or []
                        if images:
                            featured_image = images[0].get("src")
                    if not featured_image:
                        skipped_total += 1
                        continue
                    item.image_url = featured_image
                    item.save(update_fields=["image_url"])
                    updated_total += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete. Updated={updated_total} Skipped={skipped_total}"
            )
        )
