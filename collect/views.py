from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponseRedirect
from .models import RedirectLink
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt
import uuid

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
            uuid_str = metadata.get("uuid")
            amount = data.get("amount")

            if buisID is not None:
                try:
                    buisID = uuid.UUID(str(buisID))
                except (ValueError, AttributeError, TypeError):
                    buisID = None
            else:
                buisID = None

            if uuid_str is not None:
                try:
                    uuid_val = int(uuid_str)
                except (TypeError, ValueError):
                    uuid_val = None
            else:
                uuid_val = None

            # Log the reference as well as any provided sale amount
            print(amount)
            if amount is not None:
                try:
                    total_amount = float(amount) / 100  # Convert cents to dollars
                    total_amount = round(total_amount, 2)  # Optional: round to 2 decimal places
                except (TypeError, ValueError):
                    total_amount = None
                

            
            print(f"✅ Received webhook with uuid: {uuid_val} and amount: {total_amount} and buisID: {buisID}")
           

            # You can split the code if needed:
            #code = ref.split(":")[1]

            # Example: check in your DB
            # link = RedirectLink.objects.get(short_code=ref)

            response_payload = {"status": "success", "uuid": uuid_val}
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
