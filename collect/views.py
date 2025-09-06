from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponseRedirect
from .models import RedirectLink
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt
from merchants.models import MerchantMeta
from creators.models import CreatorMeta
from customer.models import CustomerMeta
from ledger.models import LedgerEntry
from decimal import Decimal, InvalidOperation

# Purchases made with this creator UUID should always incur a
# 5% commission regardless of the merchant's configured rate.
SPECIAL_CREATOR_UUID = "f5b545d4-5229-467f-8ddb-30dbb307d1ce"
SPECIAL_COMMISSION_RATE = Decimal("5")

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
                    commission_rate = (
                        SPECIAL_COMMISSION_RATE
                        if uuid == SPECIAL_CREATOR_UUID
                        else merchant_meta.affiliate_percent or 0
                    )
                    commission = round(total_amount * float(commission_rate) / 100, 2)

                    LedgerEntry.objects.create(
                        creator=creator_meta.user,
                        amount=commission,
                        entry_type="commission",
                    )
                    LedgerEntry.objects.create(
                        merchant=merchant_meta.user,
                        amount=-commission,
                        entry_type="commission",
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
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    amount_str = payload.get("total_price")

    #testing and getting the entire note attribute
    print(payload.get("note_attributes", []))

    note_attributes = {
        attr.get("name"): attr.get("value") for attr in payload.get("note_attributes", [])
    }
    uuid = note_attributes.get("uuid")  # creator uuid
    buisID = note_attributes.get("storeID")  # merchant uuid
    cusID = note_attributes.get("cusID")  # customer uuid

    print(
        f"received amount={amount_str} uuid={uuid} buisID={buisID} cusID={cusID}"
    )

    try:
        amount = Decimal(amount_str).quantize(Decimal("0.01"))
    except (TypeError, InvalidOperation):
        return JsonResponse({"error": "Invalid amount"}, status=400)

    merchant_meta = MerchantMeta.objects.filter(uuid=buisID).first()
    creator_meta = CreatorMeta.objects.filter(uuid=uuid).first()
    customer_meta = CustomerMeta.objects.filter(uuid=cusID).first()

    if merchant_meta and creator_meta:
        commission_rate = (
            SPECIAL_COMMISSION_RATE
            if uuid == SPECIAL_CREATOR_UUID
            else Decimal(merchant_meta.affiliate_percent or 0)
        )
        commission = (amount * commission_rate / Decimal("100")).quantize(
            Decimal("0.01")
        )

        if commission > 0:
            # Credit the content creator with the commission
            LedgerEntry.objects.create(
                creator=creator_meta.user,
                amount=commission,
                entry_type="commission",
            )

            # Charge the merchant for the commission
            LedgerEntry.objects.create(
                merchant=merchant_meta.user,
                amount=-commission,
                entry_type="commission",
            )

            # Reward the customer with points (60 points = $1)
            if customer_meta:
                points = int(commission * 60)
                LedgerEntry.objects.create(
                    creator=customer_meta.user,
                    amount=Decimal(points),
                    entry_type="points",
                )

    return JsonResponse({"status": "received"}, status=200)
