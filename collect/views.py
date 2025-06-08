from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponseRedirect
from .models import RedirectLink
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt

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
    print("webhook view hit")
    
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            ref = data.get("ref")
            amount = data.get("amount")

            if not ref or not ref.startswith("badger:"):
                return JsonResponse({"error": "Invalid or missing ref"}, status=400)

            # Log the reference as well as any provided sale amount
            if amount is not None:
                try:
                    total_amount = float(amount)
                except (TypeError, ValueError):
                    total_amount = None
            else:
                total_amount = None

            if total_amount is not None:
                print(f"✅ Received webhook with ref: {ref} and amount: {total_amount}")
            else:
                print(f"✅ Received webhook with ref: {ref}")

            # You can split the code if needed:
            code = ref.split(":")[1]

            # Example: check in your DB
            # link = RedirectLink.objects.get(short_code=ref)

            response_payload = {"status": "success", "ref": ref}
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
        if amount is not None:
            print(f"✅ Stripe webhook amount: {amount}")
        else:
            print("⚠️  Stripe webhook received but no amount found")

        return JsonResponse({"status": "received"}, status=200)

    return JsonResponse({"error": "Invalid method"}, status=405)
