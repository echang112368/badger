from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from uuid import uuid4

from .models import CreatorMeta

from links.models import MerchantCreatorLink
from merchants.models import MerchantItem
from collect.models import RedirectLink
from ledger.models import LedgerEntry

@login_required
def creator_dashboard(request):
    links = MerchantCreatorLink.objects.filter(creator=request.user)
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        paypal_email = request.POST.get('paypal_email', '').strip()
        if paypal_email:
            creator_meta.paypal_email = paypal_email
            creator_meta.save()

    balance = LedgerEntry.creator_balance(request.user)
    entries = LedgerEntry.objects.filter(creator=request.user).order_by('-timestamp')

    merchants_with_items = []
    for link in links:
        merchant = link.merchant
        merchant_items = []
        for item in MerchantItem.objects.filter(merchant=merchant):
            short_code = f"{request.user.id}-{item.id}"
            redirect_obj, _ = RedirectLink.objects.get_or_create(
                short_code=short_code,
                defaults={
                    'destination_url': item.link,
                    'queryParam': f"ref=badger:{uuid4()}",
                },
            )

            redirect_url = request.build_absolute_uri(
                reverse('redirect_view', args=[redirect_obj.short_code])
            )

            merchant_items.append({
                'title': item.title,
                'original_link': item.link,
                'redirect_link': redirect_url,
            })

        merchants_with_items.append({
            'merchant': merchant,
            'items': merchant_items,
        })

    return render(
        request,
        'creators/dashboard.html',
        {
            'creator': request.user,
            'creator_meta': creator_meta,
            'merchants_with_items': merchants_with_items,
            'balance': balance,
            'ledger_entries': entries,
        }
    )
