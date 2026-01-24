import json
import uuid
from urllib.parse import urlparse

from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from creators.models import CreatorMeta
from customer.models import CustomerMeta
from ledger.models import LedgerEntry
from merchants.models import MerchantItem, MerchantMeta

from .models import (
    AffiliateClick,
    RedirectLink,
    ReferralVisit,
    ReferralConversion,
    CreatorMerchantStatus,
)
from decimal import Decimal, InvalidOperation

SPECIAL_CREATOR_UUID = "733d0d67-6a30-4c48-a92e-b8e211b490f5"


def _normalise_shopify_product_id(product_id):
    if product_id is None:
        return None
    product_id_str = str(product_id).strip()
    if product_id_str.startswith("gid://"):
        parts = product_id_str.rsplit("/", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]
    return product_id_str


def _normalize_domain(domain: str) -> str:
    if not domain:
        return ""
    parsed = urlparse(domain if "://" in domain else f"//{domain}")
    host = parsed.netloc or parsed.path
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def _parse_uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _creator_is_active_for_merchant(creator_meta, merchant_meta):
    """Return whether a creator should receive payouts for a merchant."""

    if not creator_meta or not merchant_meta:
        return False

    status = CreatorMerchantStatus.objects.filter(
        creator=creator_meta, merchant=merchant_meta
    ).first()

    if status is None:
        return True

    return status.is_active


def _cors_json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type"
    return response

def _record_affiliate_click(payload):
    creator_uuid = _parse_uuid(payload.get("uuid"))
    store_uuid = _parse_uuid(payload.get("storeID"))

    if not creator_uuid:
        return _cors_json({"error": "Invalid uuid"}, status=400)

    if not store_uuid:
        return _cors_json({"error": "Invalid storeID"}, status=400)

    creator_meta = (
        CreatorMeta.objects.filter(uuid=creator_uuid)
        .select_related("user")
        .first()
    )
    if creator_meta is None:
        return _cors_json({"error": "Creator not found"}, status=404)

    merchant_meta = (
        MerchantMeta.objects.filter(uuid=store_uuid)
        .select_related("user")
        .first()
    )
    if merchant_meta is None:
        return _cors_json({"error": "Merchant not found"}, status=404)

    AffiliateClick.objects.create(uuid=creator_uuid, storeID=store_uuid)
    total_clicks = AffiliateClick.objects.filter(uuid=creator_uuid, storeID=store_uuid).count()

    return _cors_json(
        {
            "status": "click recorded",
            "creator": str(creator_uuid),
            "store": str(store_uuid),
            "total_clicks": total_clicks,
            "debug": f"received creator: {creator_uuid} and store: {store_uuid}",
        }
    )


@csrf_exempt
def track_referral_visit(request):
    if request.method == "OPTIONS":
        return _cors_json({})

    if request.method != "POST":
        return _cors_json({"error": "Invalid method"}, status=405)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _cors_json({"error": "Invalid JSON"}, status=400)

    if "uuid" in payload or "storeID" in payload:
        return _record_affiliate_click(payload)

    creator_uuid = _parse_uuid(payload.get("creator_uuid"))
    merchant_uuid = _parse_uuid(payload.get("merchant_uuid"))
    merchant_domain = payload.get("merchant_domain") or payload.get("domain") or ""
    normalized_domain = _normalize_domain(merchant_domain)

    landing_url = (payload.get("landing_url") or "")[:1024]
    landing_path = (payload.get("landing_path") or "")[:512]
    query_string = payload.get("query_string") or ""
    query_params = payload.get("query_params")
    if not isinstance(query_params, dict):
        query_params = {}
    referrer = payload.get("referrer") or ""
    visitor_id = payload.get("visitor_id") or ""
    if visitor_id:
        visitor_id = str(visitor_id)[:255]

    if not creator_uuid:
        return _cors_json({"error": "Invalid creator_uuid"}, status=400)

    creator_meta = (
        CreatorMeta.objects.filter(uuid=creator_uuid)
        .select_related("user")
        .first()
    )

    merchant_meta = None
    if merchant_uuid:
        merchant_meta = (
            MerchantMeta.objects.filter(uuid=merchant_uuid)
            .select_related("user")
            .first()
        )

    if merchant_meta is None and normalized_domain:
        merchant_meta = (
            MerchantMeta.objects.filter(shopify_store_domain__iexact=normalized_domain)
            .select_related("user")
            .first()
        )
        if merchant_meta and not merchant_uuid:
            merchant_uuid = merchant_meta.uuid

    if not merchant_uuid:
        return _cors_json({"error": "Invalid merchant_uuid"}, status=400)

    visit = ReferralVisit.objects.create(
        creator_uuid=creator_uuid,
        merchant_uuid=merchant_uuid,
        creator=creator_meta.user if creator_meta else None,
        merchant=merchant_meta.user if merchant_meta else None,
        merchant_domain=normalized_domain,
        landing_url=landing_url,
        landing_path=landing_path,
        query_string=query_string,
        query_params=query_params,
        referrer=referrer,
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        visitor_id=visitor_id,
        ip_address=_client_ip(request),
    )

    return _cors_json({"status": "ok", "visit_id": visit.id})


@csrf_exempt
def redirect_view(request, short_code):
    link = get_object_or_404(RedirectLink, short_code = short_code)
    
    redirect_url = link.destination_url

    #adds custom queryParam to redirected URL; custom link comes from redirected links in admin
    if link.queryParam:
        if  "?" in redirect_url:
            redirect_url += "&" + link.queryParam
        else:
            redirect_url += "?" + link.queryParam

    response = HttpResponseRedirect(redirect_url)
    response.set_cookie('click_id', short_code, max_age = 30*24*60*60)
    
    return response

@csrf_exempt  #Understand this more: Disable CSRF for external POSTs (safe only in dev or if authenticated)
def webhook_view(request):
    print("request entered")
    if request.method == "POST":
        try:
            payload = json.loads(request.body)

            data = payload.get("data", {}).get("object", {})
            metadata = data.get("metadata", {})

            buisID = metadata.get('buisID')
            uuid = metadata.get("uuid")
            amount = data.get("amount")

            # Ensure values are stored as strings
            def normalize_str(value):
                if value is None:
                    return None
                return str(value)

            buisID = normalize_str(buisID)
            uuid = normalize_str(uuid)

            # Log the reference as well as any provided sale amount
            print(amount)
            if amount is not None:
                try:
                    total_amount = float(amount) / 100  # Convert cents to dollars
                    total_amount = round(total_amount, 2)  # Optional: round to 2 decimal places
                except (TypeError, ValueError):
                    total_amount = None
                

            
            print(f"✅ Received webhook with uuid: {uuid} and amount: {total_amount} and buisID: {buisID}")

            if total_amount is not None and uuid and buisID:
                merchant_meta = MerchantMeta.objects.filter(uuid=buisID).first()
                creator_meta = CreatorMeta.objects.filter(uuid=uuid).first()
                if merchant_meta and creator_meta:
                    commission = Decimal("0")
                    if commission > 0:
                        LedgerEntry.objects.create(
                            creator=creator_meta.user,
                            merchant=merchant_meta.user,
                            amount=commission,
                            entry_type=LedgerEntry.EntryType.COMMISSION,
                        )
                        merchant_entry_type = (
                            LedgerEntry.EntryType.BADGER_PAYOUT
                            if str(creator_meta.uuid) == SPECIAL_CREATOR_UUID
                            else LedgerEntry.EntryType.AFFILIATE_PAYOUT
                        )
                        LedgerEntry.objects.create(
                            merchant=merchant_meta.user,
                            amount=-commission,
                            entry_type=merchant_entry_type,
                        )
           

            # You can split the code if needed:
            #code = ref.split(":")[1]

            # Example: check in your DB
            # link = RedirectLink.objects.get(short_code=ref)

            response_payload = {"status": "success", "uuid": uuid}
            if total_amount is not None:
                response_payload["amount"] = total_amount

            return JsonResponse(response_payload, status=200)

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

    return JsonResponse({"error": "Invalid method"}, status=405)

@csrf_exempt
def stripe_webhook_view(request):
    if request.method == "POST":
        try:
            event = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        data_object = event.get("data", {}).get("object", {}) if isinstance(event, dict) else {}
        amount = data_object.get("amount_total") or data_object.get("amount")
        metadata = data_object.get("metadata", {}) if isinstance(data_object, dict) else {}
        ref = metadata.get("ref")

        if amount is not None:
            print(f"✅ Stripe webhook amount: {amount}")
        else:
            print("⚠️  Stripe webhook received but no amount found")

        context = {"ref": ref, "amount": amount}
        return render(request, "collect/stripe_webhook.html", context)

    return JsonResponse({"error": "Invalid method"}, status=405)


@csrf_exempt
def orders_create_webhook(request):
    print('orders_create_webhook called' )
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)

    try:
        payload = json.loads(request.body)
        order_id = payload.get("id")
        print(f"Processing order {order_id}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    amount_str = payload.get("total_price")
    discount_str = payload.get("total_discounts") or payload.get("total_discount")

    #testing and getting the entire note attribute
    print(payload.get("note_attributes", []))

    note_attributes = {
        attr.get("name"): attr.get("value") for attr in payload.get("note_attributes", [])
    }
    creator_uuid_raw = note_attributes.get("uuid")  # creator uuid
    merchant_uuid_raw = note_attributes.get("storeID")  # merchant uuid
    customer_uuid_raw = note_attributes.get("cusID")  # customer uuid

    creator_uuid = _parse_uuid(creator_uuid_raw)
    merchant_uuid = _parse_uuid(merchant_uuid_raw)
    customer_uuid = _parse_uuid(customer_uuid_raw)

    print(
        "received amount={} uuid={} buisID={} cusID={}".format(
            amount_str, creator_uuid_raw, merchant_uuid_raw, customer_uuid_raw
        )
    )

    merchant_meta = (
        MerchantMeta.objects.filter(uuid=merchant_uuid).first()
        if merchant_uuid
        else None
    )
    creator_meta = (
        CreatorMeta.objects.filter(uuid=creator_uuid).first()
        if creator_uuid
        else None
    )
    customer_meta = (
        CustomerMeta.objects.filter(uuid=customer_uuid).first()
        if customer_uuid
        else None
    )

    if merchant_meta and not merchant_meta.has_active_billing_plan:
        return JsonResponse(
            {
                "error": "Merchant does not have an active billing plan.",
                "status": merchant_meta.shopify_billing_status,
            },
            status=402,
        )

    commission_total = Decimal("0")
    order_total = Decimal("0")
    discount_total = Decimal("0")

    creator_uuid_str = str(creator_uuid) if creator_uuid else None
    creator_active_for_merchant = _creator_is_active_for_merchant(
        creator_meta, merchant_meta
    )

    if amount_str:
        try:
            order_total = Decimal(str(amount_str)).quantize(Decimal("0.01"))
        except (TypeError, InvalidOperation):
            order_total = Decimal("0")

    if discount_str:
        try:
            discount_total = Decimal(str(discount_str)).quantize(Decimal("0.01"))
        except (TypeError, InvalidOperation):
            discount_total = Decimal("0")

    if (
        creator_active_for_merchant
        and creator_uuid_str == SPECIAL_CREATOR_UUID
        and amount_str
    ):
        try:
            commission_total = (
                Decimal(amount_str) * Decimal("0.05")
            ).quantize(Decimal("0.01"))
            print(
                f"Special UUID detected, overriding commission to {commission_total}"
            )
        except (TypeError, InvalidOperation):
            commission_total = Decimal("0")
    elif merchant_meta and creator_meta and creator_active_for_merchant:
        # Calculate commission per line item based on its group percentage
        for item in payload.get("line_items", []):
            product_id = _normalise_shopify_product_id(item.get("product_id"))
            quantity = item.get("quantity", 1)
            price = item.get("price")
            try:
                line_amount = (Decimal(price) * Decimal(quantity)).quantize(
                    Decimal("0.01")
                )
            except (TypeError, InvalidOperation):
                continue

            merchant_item = MerchantItem.objects.filter(
                merchant=merchant_meta.user, shopify_product_id=product_id
            ).first()
            group = merchant_item.groups.first() if merchant_item else None

            if group:
                rate = Decimal(group.affiliate_percent or 0)
            else:
                rate = Decimal("0")

            commission = (line_amount * rate / Decimal("100")).quantize(
                Decimal("0.01")
            )
            print(
                f"Item {product_id}: qty={quantity}, amount={line_amount}, "
                f"rate={rate}%, commission={commission}"
            )
            commission_total += commission

    commission_total = commission_total.quantize(Decimal("0.01"))
    print(f"Order {order_id} total commission: {commission_total}")

    conversion_metadata = {"source": "shopify_orders_create"}
    line_items_payload = []
    for item in payload.get("line_items", []):
        product_id = _normalise_shopify_product_id(item.get("product_id"))
        if not product_id:
            continue
        line_items_payload.append(
            {
                "product_id": str(product_id),
                "quantity": item.get("quantity", 1),
                "price": item.get("price"),
            }
        )
    if line_items_payload:
        conversion_metadata["line_items"] = line_items_payload
    if customer_uuid:
        conversion_metadata["customer_uuid"] = str(customer_uuid)

    if merchant_meta and creator_meta and creator_active_for_merchant:
        ReferralConversion.objects.create(
            creator_uuid=creator_uuid or creator_meta.uuid,
            merchant_uuid=merchant_uuid or merchant_meta.uuid,
            creator=creator_meta.user,
            merchant=merchant_meta.user,
            order_id=str(order_id) if order_id else "",
            order_amount=order_total,
            commission_amount=commission_total,
            metadata=conversion_metadata,
        )

    if (
        merchant_meta
        and creator_meta
        and creator_active_for_merchant
        and commission_total > 0
    ):
        # Credit the content creator with the commission
        LedgerEntry.objects.create(
            creator=creator_meta.user,
            merchant=merchant_meta.user,
            amount=commission_total,
            entry_type=LedgerEntry.EntryType.COMMISSION,
        )

        # Charge the merchant for the commission
        merchant_entry_type = (
            LedgerEntry.EntryType.BADGER_PAYOUT
            if creator_uuid_str == SPECIAL_CREATOR_UUID
            else LedgerEntry.EntryType.AFFILIATE_PAYOUT
        )
        LedgerEntry.objects.create(
            merchant=merchant_meta.user,
            amount=-commission_total,
            entry_type=merchant_entry_type,
        )

    # Reward the customer with points ($1 spent = 60 points; 60 points redeem for $0.10)
    if customer_meta and order_total > 0:
        points = int(order_total * 60)
        LedgerEntry.objects.create(
            creator=customer_meta.user,
            amount=Decimal(points),
            entry_type="points",
        )

    if customer_meta and discount_total > 0:
        LedgerEntry.objects.create(
            creator=customer_meta.user,
            amount=discount_total,
            entry_type=LedgerEntry.EntryType.SAVINGS,
        )

    return JsonResponse({"status": "received"}, status=200)
