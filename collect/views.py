from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponseRedirect
from .models import RedirectLink
from django.http import JsonResponse
import json
from django.views.decorators.csrf import csrf_exempt

def redirect_view(request, short_code):
    link = get_object_or_404(RedirectLink, short_code = short_code)
    
    response = HttpResponseRedirect(link.destination_url)
    response.set_cookie('click_id', short_code, max_age = 30*24*60*60)
    
    return response

def webhook_view(request):
    if request.method == "POST":
        data = json.loads(request.body)
        ref = data.get("ref")
        order_id = data.get("order_id")
        amount = data.get("amount")

        try: 
            link = RedirectLink.objects.get(short_code=ref)
            print(f"Purchase made from: {ref}, Amount: ${amount}, Order ID: {order_id}")
            # Optionally create a Conversion object here
            return JsonResponse({"status": "success"}, status=200)
        
        except RedirectLink.DoesNotExist:
            return JsonResponse({"error": "Invalid referral code"}, status=404)

    return JsonResponse({"error": "Invalid method"}, status=405)

