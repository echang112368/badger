from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from links.models import MerchantCreatorLink

@login_required
def creator_dashboard(request):
    links = MerchantCreatorLink.objects.filter(creator=request.user)
    merchants = [link.merchant for link in links]  # ensure linked merchant exists
    return render(request, 'creators/dashboard.html', {
        'creator': request.user,
        'merchants': merchants,
    })