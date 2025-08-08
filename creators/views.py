from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import date
import json

from .models import CreatorMeta

from links.models import MerchantCreatorLink, STATUS_ACTIVE
from merchants.models import MerchantItem, MerchantMeta
from urllib.parse import urlparse, urlunparse
from ledger.models import LedgerEntry


@login_required
def creator_earnings(request):
    balance = LedgerEntry.creator_balance(request.user)
    entries = LedgerEntry.objects.filter(creator=request.user).order_by("-timestamp")
    monthly_data = (
        LedgerEntry.objects.filter(creator=request.user, entry_type="commission")
        .annotate(month=TruncMonth("timestamp"))
        .values("month")
        .annotate(total=Sum("amount"))
    )
    monthly_totals = {d["month"].date(): float(d["total"]) for d in monthly_data}
    now = timezone.now()
    year = now.year
    month = now.month
    months = []
    for _ in range(12):
        months.append(date(year, month, 1))
        if month == 1:
            month = 12
            year -= 1
        else:
            month -= 1
    months.reverse()
    earnings_labels = [m.strftime("%b %Y") for m in months]
    earnings_totals = [monthly_totals.get(m, 0.0) for m in months]
    return render(
        request,
        "creators/earnings.html",
        {
            "balance": balance,
            "ledger_entries": entries,
            "earnings_labels": json.dumps(earnings_labels),
            "earnings_totals": json.dumps(earnings_totals),
        },
    )


@login_required
def creator_affiliate_companies(request):
    active_links = MerchantCreatorLink.objects.filter(
        creator=request.user, status=STATUS_ACTIVE
    )

    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)

    merchants_with_items = []
    for link in active_links:
        merchant = link.merchant
        merchant_meta, _ = MerchantMeta.objects.get_or_create(user=merchant)
        merchant_items = []
        for item in MerchantItem.objects.filter(merchant=merchant):
            base_url = item.link
            parsed = urlparse(base_url)
            existing_query = parsed.query
            query_param = (
                f"ref=badger:{creator_meta.uuid};buisID:{merchant_meta.uuid}"
            )
            if existing_query:
                new_query = existing_query + "&" + query_param
            else:
                new_query = query_param
            full_url = urlunparse(parsed._replace(query=new_query))
            merchant_items.append({"title": item.title, "custom_url": full_url})

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
        return redirect('creator_affiliate_companies')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'accept':
            link.status = STATUS_ACTIVE
            link.save()
        elif action == 'decline':
            link.delete()

    return redirect('creator_affiliate_companies')
