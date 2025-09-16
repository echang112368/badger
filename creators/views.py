from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import Sum, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import date
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .models import CreatorMeta
from .forms import CreatorSettingsForm
from accounts.forms import UserNameForm

from links.models import (
    MerchantCreatorLink,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_REQUESTED,
)
from merchants.models import MerchantMeta, ItemGroup, MerchantItem
from accounts.models import CustomUser
from ledger.models import LedgerEntry


_SETTINGS_TABS = {"profile", "billing", "notifications", "integrations", "api"}


def _resolve_settings_tab(tab: Optional[str]) -> str:
    """Return a valid settings tab slug."""

    if not tab:
        return "profile"
    tab = tab.strip().lower()
    return tab if tab in _SETTINGS_TABS else "profile"


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
    active_links = list(
        MerchantCreatorLink.objects.filter(
            creator=request.user, status=STATUS_ACTIVE
        ).select_related("merchant__merchantmeta")
    )
    inactive_links = list(
        MerchantCreatorLink.objects.filter(
            creator=request.user, status=STATUS_INACTIVE
        ).select_related("merchant__merchantmeta")
    )
    pending_links = list(
        MerchantCreatorLink.objects.filter(
            creator=request.user, status=STATUS_REQUESTED
        )
        .select_related("merchant__merchantmeta")
        .order_by("merchant__username")
    )

    def merchant_display_name(merchant):
        meta = getattr(merchant, "merchantmeta", None)
        if meta and meta.company_name:
            return meta.company_name
        return merchant.username

    def quantize_amount(value):
        if value is None:
            value = Decimal("0")
        elif not isinstance(value, Decimal):
            value = Decimal(value)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    start_of_month = timezone.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    def build_company_entry(link):
        merchant = link.merchant
        commission_entries = LedgerEntry.objects.filter(
            creator=request.user,
            merchant=merchant,
            entry_type="commission",
        )
        total_raw = commission_entries.aggregate(total=Sum("amount"))["total"]
        total_earnings = quantize_amount(total_raw)
        monthly_raw = commission_entries.filter(timestamp__gte=start_of_month).aggregate(
            total=Sum("amount")
        )["total"]
        monthly_earnings = quantize_amount(monthly_raw)
        clicks = getattr(link, "clicks", 0) or 0
        conversions = commission_entries.filter(amount__gt=0).count()
        if clicks:
            avg = (total_earnings / Decimal(clicks)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            conversion_rate = (
                (Decimal(conversions) / Decimal(clicks)) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            avg = Decimal("0.00")
            conversion_rate = Decimal("0.00")

        return {
            "link_id": link.id,
            "name": merchant_display_name(merchant),
            "email": merchant.email,
            "monthly_earnings": monthly_earnings,
            "total_earnings": total_earnings,
            "clicks": clicks,
            "avg_earnings_per_click": avg,
            "conversion_rate": conversion_rate,
        }

    active_companies = [build_company_entry(link) for link in active_links]
    inactive_companies = [build_company_entry(link) for link in inactive_links]

    pending_requests = [
        {
            "id": link.id,
            "name": merchant_display_name(link.merchant),
            "email": link.merchant.email,
        }
        for link in pending_links
    ]

    return render(
        request,
        "creators/affiliate_companies.html",
        {
            "active_companies": active_companies,
            "inactive_companies": inactive_companies,
            "pending_requests": pending_requests,
        },
    )


@login_required
def creator_my_links(request, merchant_id=None, group_id=None):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    links = MerchantCreatorLink.objects.filter(
        creator=request.user, status=STATUS_ACTIVE
    ).select_related("merchant")

    breadcrumbs = [("Company", None)]
    context = {"breadcrumbs": breadcrumbs}
    query = request.GET.get("q", "").strip()

    # Top level: show companies or search across all items
    if merchant_id is None:
        if query:
            merchant_ids = links.values_list("merchant_id", flat=True)
            item_queryset = MerchantItem.objects.filter(
                groups__merchant_id__in=merchant_ids
            ).distinct()
            item_queryset = item_queryset.filter(
                Q(title__icontains=query)
                | Q(shopify_product_id__icontains=query)
                | Q(id__icontains=query)
            )

            merchant_ids_found = set()
            group_ids_found = set()
            items = []
            for item in item_queryset:
                merchant_ids_found.add(item.merchant_id)
                group_ids_found.update(item.groups.values_list("id", flat=True))
                base_link = item.link
                sep = "&" if "?" in base_link else "?"
                affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
                if item.shopify_product_id:
                    affiliate_link += f"&item_id={item.shopify_product_id}"
                items.append({"item": item, "affiliate_link": affiliate_link})

            # If search results are within a single merchant or group, redirect so
            # breadcrumbs show the full path.
            if len(group_ids_found) == 1 and len(merchant_ids_found) == 1:
                merchant_id = next(iter(merchant_ids_found))
                group_id = next(iter(group_ids_found))
                return redirect(
                    "creator_my_links_group",
                    merchant_id=merchant_id,
                    group_id=group_id,
                )
            if len(merchant_ids_found) == 1:
                merchant_id = next(iter(merchant_ids_found))
                return redirect("creator_my_links_merchant", merchant_id=merchant_id)

            context.update({"items": items, "search_query": query})
            return render(request, "creators/my_links.html", context)

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
        context.update({"companies": companies, "search_query": query})
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

    # Merchant level: show groups or search across merchant items
    if group_id is None:
        if query:
            item_queryset = MerchantItem.objects.filter(
                groups__merchant=merchant
            ).distinct()
            item_queryset = item_queryset.filter(
                Q(title__icontains=query)
                | Q(shopify_product_id__icontains=query)
                | Q(id__icontains=query)
            )
            items = []
            for item in item_queryset:
                base_link = item.link
                sep = "&" if "?" in base_link else "?"
                affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
                if item.shopify_product_id:
                    affiliate_link += f"&item_id={item.shopify_product_id}"
                items.append({"item": item, "affiliate_link": affiliate_link})
            context.update(
                {"merchant": merchant, "items": items, "search_query": query}
            )
            return render(request, "creators/my_links.html", context)

        groups = ItemGroup.objects.filter(merchant=merchant).prefetch_related("items")
        context.update({"merchant": merchant, "groups": groups, "search_query": query})
        return render(request, "creators/my_links.html", context)

    group = get_object_or_404(ItemGroup, id=group_id, merchant=merchant)
    breadcrumbs[-1] = (
        merchant_name,
        reverse("creator_my_links_merchant", args=[merchant.id]),
    )
    breadcrumbs.append((group.name, None))

    item_queryset = group.items.all()
    if query:
        item_queryset = item_queryset.filter(
            Q(title__icontains=query)
            | Q(shopify_product_id__icontains=query)
            | Q(id__icontains=query)
        )

    items = []
    for item in item_queryset:
        base_link = item.link
        sep = "&" if "?" in base_link else "?"
        affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
        if item.shopify_product_id:
            affiliate_link += f"&item_id={item.shopify_product_id}"
        items.append({"item": item, "affiliate_link": affiliate_link})

    context.update(
        {
            "merchant": merchant,
            "group": group,
            "items": items,
            "search_query": query,
        }
    )
    return render(request, "creators/my_links.html", context)


@login_required
def creator_settings(request):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        active_tab = _resolve_settings_tab(request.POST.get("active_tab"))
        settings_form = CreatorSettingsForm(request.POST, instance=creator_meta)
        user_form = UserNameForm(request.POST, instance=request.user)
        settings_valid = settings_form.is_valid()
        user_valid = user_form.is_valid()

        if settings_valid:
            settings_form.save()
        if user_valid:
            user_form.save()

        if settings_valid and user_valid:
            redirect_url = reverse("creator_settings")
            if active_tab != "profile":
                redirect_url = f"{redirect_url}?tab={active_tab}"
            return redirect(redirect_url)
    else:
        active_tab = _resolve_settings_tab(request.GET.get("tab"))
        settings_form = CreatorSettingsForm(instance=creator_meta)
        user_form = UserNameForm(instance=request.user)

    return render(
        request,
        "creators/settings.html",
        {
            "creator_meta": creator_meta,
            "creator": request.user,
            "settings_form": settings_form,
            "user_form": user_form,
            "active_tab": active_tab,
        },
    )


@login_required
def creator_support(request):
    return render(request, "creators/support.html")


@login_required
def delete_affiliate_merchants(request):
    if request.method == "POST":
        link_ids = request.POST.getlist("selected_links")
        if link_ids:
            MerchantCreatorLink.objects.filter(
                id__in=link_ids, creator=request.user
            ).delete()
    return redirect("creator_affiliate_companies")


@login_required
def respond_request(request, link_id):
    try:
        link = MerchantCreatorLink.objects.get(id=link_id, creator=request.user)
    except MerchantCreatorLink.DoesNotExist:
        return redirect("creator_affiliate_companies")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "accept":
            link.status = STATUS_ACTIVE
            link.save()
        elif action == "decline":
            link.delete()

    return redirect("creator_affiliate_companies")
