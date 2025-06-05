from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .models import MerchantCreatorLink

@login_required
@require_GET
def link_summary(request):
    user = request.user
    if user.is_merchant:
        links = MerchantCreatorLink.objects.filter(merchant=user)
        counterparties = [{'creator': link.creator.username, 'status': link.status} for link in links]
    elif user.is_creator:
        links = MerchantCreatorLink.objects.filter(creator=user)
        counterparties = [{'merchant': link.merchant.username, 'status': link.status} for link in links]
    else:
        counterparties = []

    return JsonResponse({'linked_users': counterparties})
