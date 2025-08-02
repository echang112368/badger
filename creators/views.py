from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from uuid import uuid4

from .models import CreatorMeta

from links.models import MerchantCreatorLink, STATUS_ACTIVE
from merchants.models import MerchantItem
from collect.models import RedirectLink
from ledger.models import LedgerEntry


@login_required
def creator_dashboard(request):
    balance = LedgerEntry.creator_balance(request.user)
    entries = LedgerEntry.objects.filter(creator=request.user).order_by("-timestamp")
    return render(
        request,
        "creators/dashboard.html",
        {
            "creator": request.user,
            "balance": balance,
            "ledger_entries": entries,
        },
    )


@login_required
def creator_earnings(request):
    balance = LedgerEntry.creator_balance(request.user)
    entries = LedgerEntry.objects.filter(creator=request.user).order_by("-timestamp")
    return render(
        request,
        "creators/earnings.html",
        {
            "balance": balance,
            "ledger_entries": entries,
        },
    )


@login_required
def creator_affiliate_companies(request):
    active_links = MerchantCreatorLink.objects.filter(
        creator=request.user, status=STATUS_ACTIVE
    )

    merchants_with_items = []
    for link in active_links:
        merchant = link.merchant
        merchant_items = []
        for item in MerchantItem.objects.filter(merchant=merchant):
            short_code = f"{request.user.id}-{item.id}"
            redirect_obj, _ = RedirectLink.objects.get_or_create(
                short_code=short_code,
                defaults={
                    "destination_url": item.link,
                    "queryParam": f"ref=badger:{uuid4()}",
                },
            )
            redirect_url = request.build_absolute_uri(
                reverse("redirect_view", args=[redirect_obj.short_code])
            )
            merchant_items.append(
                {
                    "title": item.title,
                    "original_link": item.link,
                    "redirect_link": redirect_url,
                }
            )

        merchants_with_items.append({"merchant": merchant, "items": merchant_items})

    return render(
        request,
        "creators/affiliate_companies.html",
        {"merchants_with_items": merchants_with_items},
    )


@login_required
def creator_my_links(request):
    return render(request, "creators/my_links.html")


@login_required
def creator_settings(request):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        paypal_email = request.POST.get("paypal_email", "").strip()
        if paypal_email:
            creator_meta.paypal_email = paypal_email
            creator_meta.save()

    return render(
        request, "creators/settings.html", {"creator_meta": creator_meta}
    )


@login_required
def creator_support(request):
    return render(request, "creators/support.html")


@login_required
def respond_request(request, link_id):
    try:
        link = MerchantCreatorLink.objects.get(id=link_id, creator=request.user)
    except MerchantCreatorLink.DoesNotExist:
        return redirect('creator_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'accept':
            link.status = STATUS_ACTIVE
            link.save()
        elif action == 'decline':
            link.delete()

    return redirect('creator_dashboard')
