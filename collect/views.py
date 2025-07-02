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
    print("request entered")
    if request.method == "POST":
        try:
            payload = json.loads(request.body)

            data = payload.get("data", {}).get("object", {})
            metadata = data.get("metadata", {})

            buisID = metadata.get('buisID')
            uuid = metadata.get("uuid")
            amount = data.get("amount")
            
            if buisID is not None:
                try:
                    buisID = int(buisID)
                except (TypeError, ValueError):
                    buisID = None
            else:
                buisID = None

            if uuid is not None:
                try:
                    uuid = int(uuid)
                except (TypeError, ValueError):
                    uuid = None
            else:
                uuid = None

            # Log the reference as well as any provided sale amount
            print(amount)
            if amount is not None:
                try:
                    total_amount = float(amount) / 100  # Convert cents to dollars
                    total_amount = round(total_amount, 2)  # Optional: round to 2 decimal places
                except (TypeError, ValueError):
                    total_amount = None
                

            
            print(f"✅ Received webhook with uuid: {uuid} and amount: {total_amount} and buisID: {buisID}")
           

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