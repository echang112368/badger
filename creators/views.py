from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import date
import json

from .models import CreatorMeta
from accounts.forms import UserNameForm

from links.models import MerchantCreatorLink, STATUS_ACTIVE, STATUS_REQUESTED
from merchants.models import MerchantItem, MerchantMeta, ItemGroup
from accounts.models import CustomUser
from collect.models import RedirectLink
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
    pending_links = MerchantCreatorLink.objects.filter(
        creator=request.user, status=STATUS_REQUESTED
    )

    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)

    merchants_with_items = []
    for link in active_links:
        merchant = link.merchant
        merchant_meta, _ = MerchantMeta.objects.get_or_create(user=merchant)
        merchant_items = []
        for item in MerchantItem.objects.filter(merchant=merchant):
            short_code = f"{request.user.id}-{item.id}"
            query_param = f"ref=badger:{creator_meta.uuid};buisID:{merchant_meta.uuid}"
            redirect_obj, _ = RedirectLink.objects.get_or_create(
                short_code=short_code,
                defaults={
                    "destination_url": item.link,
                    "queryParam": query_param,
                },
            )
            if (
                redirect_obj.destination_url != item.link
                or redirect_obj.queryParam != query_param
            ):
                redirect_obj.destination_url = item.link
                redirect_obj.queryParam = query_param
                redirect_obj.save()
            redirect_url = request.build_absolute_uri(
                reverse("redirect_view", args=[redirect_obj.short_code])
            )
            merchant_items.append({"title": item.title, "redirect_link": redirect_url})

        merchants_with_items.append({"merchant": merchant, "items": merchant_items})

    return render(
        request,
        "creators/affiliate_companies.html",
        {"merchants_with_items": merchants_with_items, "pending_links": pending_links},
    )


@login_required
def creator_my_links(request, merchant_id=None, group_id=None):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    links = (
        MerchantCreatorLink.objects.filter(creator=request.user, status=STATUS_ACTIVE)
        .select_related("merchant")
    )

    breadcrumbs = [("Company", None)]
    context = {"breadcrumbs": breadcrumbs}

    if merchant_id is None:
        companies = []
        for link in links:
            merchant = link.merchant
            merchant_meta = MerchantMeta.objects.filter(user=merchant).first()
            name = (
                merchant_meta.company_name
                if merchant_meta and merchant_meta.company_name
                else merchant.username
            )
            companies.append({"id": merchant.id, "name": name})
        context["companies"] = companies
        return render(request, "creators/my_links.html", context)

    merchant = get_object_or_404(CustomUser, id=merchant_id)
    if not links.filter(merchant_id=merchant.id).exists():
        return redirect("creator_my_links")

    merchant_meta = MerchantMeta.objects.filter(user=merchant).first()
    merchant_name = (
        merchant_meta.company_name
        if merchant_meta and merchant_meta.company_name
        else merchant.username
    )
    breadcrumbs[0] = ("Company", reverse("creator_my_links"))
    breadcrumbs.append((merchant_name, None))

    if group_id is None:
        groups = ItemGroup.objects.filter(merchant=merchant).prefetch_related("items")
        context.update({"merchant": merchant, "groups": groups})
        return render(request, "creators/my_links.html", context)

    group = get_object_or_404(ItemGroup, id=group_id, merchant=merchant)
    breadcrumbs[-1] = (
        merchant_name,
        reverse("creator_my_links_merchant", args=[merchant.id]),
    )
    breadcrumbs.append((group.name, None))

    items = []
    for item in group.items.all():
        base_link = item.link
        sep = "&" if "?" in base_link else "?"
        affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
        items.append({"item": item, "affiliate_link": affiliate_link})

    context.update({"merchant": merchant, "group": group, "items": items})
    return render(request, "creators/my_links.html", context)


@login_required
def creator_settings(request):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        user_form = UserNameForm(request.POST, instance=request.user)
        paypal_email = request.POST.get("paypal_email", "").strip()
        if user_form.is_valid():
            user_form.save()
            if paypal_email:
                creator_meta.paypal_email = paypal_email
                creator_meta.save()
            return redirect("creator_settings")
    else:
        user_form = UserNameForm(instance=request.user)

    return render(
        request,
        "creators/settings.html",
        {"creator_meta": creator_meta, "creator": request.user, "user_form": user_form},
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
