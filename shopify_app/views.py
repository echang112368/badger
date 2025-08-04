import json
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from .forms import ShopifySettingsForm
from .models import ShopifyCredential
from merchants.models import MerchantMeta
from .shopify_api import post_collect_webhook


@login_required
def shopify_settings(request):
    merchant_meta, _ = MerchantMeta.objects.get_or_create(user=request.user)
    credential, _ = ShopifyCredential.objects.get_or_create(merchant=merchant_meta)

    if request.method == "POST":
        form = ShopifySettingsForm(request.POST, instance=credential)
        if form.is_valid():
            form.save()
            return redirect("shopify_settings")
    else:
        form = ShopifySettingsForm(instance=credential)

    return render(request, "shopify_app/settings.html", {"form": form})


def referral_script(request):
    js = """
    (function(){
        function storeReferral(){
            var params = new URLSearchParams(window.location.search);
            var ref = params.get('ref');
            if(ref){
                var creatorMatch = ref.match(/badger:([^;]+)/);
                var merchantMatch = ref.match(/buisID:([^;]+)/);
                if(creatorMatch){
                    localStorage.setItem('badger_creator_uuid', creatorMatch[1]);
                }
                if(merchantMatch){
                    localStorage.setItem('badger_merchant_uuid', merchantMatch[1]);
                }
            }
        }
        function persistCart(){
            var creator = localStorage.getItem('badger_creator_uuid');
            var merchant = localStorage.getItem('badger_merchant_uuid');
            if(!creator || !merchant){ return; }
            if(window.fetch){
                try{
                    fetch('/cart/update.js', {
                        method: 'POST',
                        headers: {'Content-Type':'application/json'},
                        body: JSON.stringify({attributes:{creatorUUID:creator, merchantUUID:merchant}})
                    });
                }catch(e){ }
            }
        }
        storeReferral();
        persistCart();
    })();
    """
    return HttpResponse(js, content_type="application/javascript")


@csrf_exempt
def order_webhook(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)
    try:
        order = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    creator_uuid = None
    merchant_uuid = None
    for attr in order.get("note_attributes", []):
        if attr.get("name") == "creatorUUID":
            creator_uuid = attr.get("value")
        elif attr.get("name") == "merchantUUID":
            merchant_uuid = attr.get("value")

    if creator_uuid and merchant_uuid:
        order_id = order.get("id")
        total_price = order.get("total_price")
        currency = order.get("currency", "USD")
        timestamp = order.get("created_at")
        post_collect_webhook(creator_uuid, merchant_uuid, order_id, total_price, currency, timestamp)

    return JsonResponse({"status": "ok"})
