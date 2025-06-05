from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from links.models import MerchantCreatorLink

@login_required
def merchant_dashboard(request):
    links = MerchantCreatorLink.objects.filter(merchant=request.user)
    creators = [link.creator for link in links]
    return render(request, 'merchants/dashboard.html', {
        'merchant': request.user,
        'creators': creators,
    })

