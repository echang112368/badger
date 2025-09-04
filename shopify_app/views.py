import uuid
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from merchants.models import MerchantMeta
from .shopify_client import ShopifyClient

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_discount(request, merchant_uuid):
    """Create a unique 3% discount code valid for 24 hours.

    Returns JSON with the generated coupon code.
    """
    try:
        meta = MerchantMeta.objects.get(uuid=merchant_uuid)
    except MerchantMeta.DoesNotExist:
        return Response({"error": "Invalid merchant"}, status=status.HTTP_400_BAD_REQUEST)

    if not meta.shopify_access_token or not meta.shopify_store_domain:
        return Response(
            {"error": "Missing Shopify credentials"},
            status=status.HTTP_400_BAD_REQUEST,
        )

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
    client.post(
        f"/admin/api/2024-07/price_rules/{price_rule_id}/discount_codes.json",
        json=discount_payload,
    )

    print(coupon_code)
    return Response({"coupon_code": coupon_code})
