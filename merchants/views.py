from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from merchants.models import MerchantCreatorLink

@login_required
def merchant_dashboard(request):
    if not request.user.is_merchant:
        return HttpResponseForbidden("Access denied.")

    # Get all linked creators for this merchant
    links = MerchantCreatorLink.objects.filter(merchant=request.user).select_related('creator')
    creators = [link.creator for link in links]

    return render(request, 'merchants/dashboard.html', {
        'merchant': request.user,
        'creators': creators
    })
