from decimal import Decimal, InvalidOperation
from typing import Iterable, Tuple

from merchants.models import MerchantItem


def _extract_line_items(metadata: dict) -> Iterable[dict]:
    if not isinstance(metadata, dict):
        return []
    line_items = metadata.get("line_items", [])
    if isinstance(line_items, list):
        return line_items
    return []


def compute_commission_schedule(conversion, merchant) -> Iterable[Tuple[Decimal, int]]:
    line_items = list(_extract_line_items(conversion.metadata))
    if not line_items:
        return [(conversion.commission_amount, 0)]

    product_ids = []
    for item in line_items:
        product_id = item.get("product_id")
        if product_id is None:
            continue
        product_ids.append(str(product_id))

    items_by_product = {}
    if product_ids:
        for item in (
            MerchantItem.objects.filter(
                merchant=merchant, shopify_product_id__in=product_ids
            ).prefetch_related("groups")
        ):
            items_by_product[str(item.shopify_product_id)] = item

    breakdown = []
    for item in line_items:
        product_id = item.get("product_id")
        if product_id is None:
            continue
        quantity = item.get("quantity", 1)
        price = item.get("price")
        try:
            line_amount = (Decimal(str(price)) * Decimal(str(quantity))).quantize(
                Decimal("0.01")
            )
        except (TypeError, InvalidOperation):
            continue

        merchant_item = items_by_product.get(str(product_id))
        group = merchant_item.groups.first() if merchant_item else None
        rate = Decimal(group.affiliate_percent or 0) if group else Decimal("0")
        return_policy_days = group.return_policy_days if group else 0
        commission = (line_amount * rate / Decimal("100")).quantize(Decimal("0.01"))
        breakdown.append((commission, return_policy_days))

    if not breakdown:
        return [(conversion.commission_amount, 0)]

    return breakdown
