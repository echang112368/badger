from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from links.models import MerchantCreatorLink
from merchants.models import MerchantItem

@login_required
def creator_dashboard(request):
    links = MerchantCreatorLink.objects.filter(creator=request.user)

    merchants_with_items = []
    for link in links:
        merchant = link.merchant
        items = MerchantItem.objects.filter(merchant=merchant)
        merchants_with_items.append({
            'merchant': merchant,
            'items': items,
        })

    return render(
        request,
        'creators/dashboard.html',
        {
            'creator': request.user,
            'merchants_with_items': merchants_with_items,
        }
    )
