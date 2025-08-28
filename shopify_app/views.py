import uuid
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.http import require_POST
from merchants.models import MerchantMeta
from .shopify_client import ShopifyClient

@require_POST
def create_discount(request, merchant_uuid):
    """Create a unique 3% discount code valid for 24 hours.

    Returns JSON with the generated coupon code.
    """
    try:
        meta = MerchantMeta.objects.get(uuid=merchant_uuid)
    except MerchantMeta.DoesNotExist:
        return HttpResponseBadRequest("Invalid merchant")

    if not meta.shopify_access_token or not meta.shopify_store_domain:
        return HttpResponseBadRequest("Missing Shopify credentials")

    client = ShopifyClient(meta.shopify_access_token, meta.shopify_store_domain)

    coupon_code = f"BADGER-{uuid.uuid4().hex[:8].upper()}"
    now = timezone.now()
    price_rule_payload = {
        "price_rule": {
            "title": coupon_code,
            "target_type": "line_item",
            "target_selection": "all",
            "allocation_method": "across",
            "value_type": "percentage",
            "value": "-3.0",
            "customer_selection": "all",
            "starts_at": now.isoformat(),
            "ends_at": (now + timezone.timedelta(days=1)).isoformat(),
            "usage_limit": 1,
        }
    }

    response = client.post("/admin/api/2024-07/price_rules.json", json=price_rule_payload)
    price_rule_id = response.json()["price_rule"]["id"]

    discount_payload = {"discount_code": {"code": coupon_code}}
    client.post(f"/admin/api/2024-07/price_rules/{price_rule_id}/discount_codes.json", json=discount_payload)

    print(coupon_code)
    return JsonResponse({"coupon_code": coupon_code})
