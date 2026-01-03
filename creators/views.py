from decimal import Decimal, InvalidOperation
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import Sum, Q, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .models import CreatorMeta
from accounts.forms import UserNameForm
from collect.models import AffiliateClick, ReferralVisit, ReferralConversion

from links.models import (
    MerchantCreatorLink,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_REQUESTED,
)
from merchants.models import MerchantMeta, ItemGroup, MerchantItem
from shopify_app.shopify_client import ShopifyClient
from accounts.models import CustomUser
from ledger.models import LedgerEntry

logger = logging.getLogger(__name__)

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
            "earnings_labels": earnings_labels,
            "earnings_totals": earnings_totals,
        },
    )


def _merchant_display_name(merchant):
    meta = getattr(merchant, "merchantmeta", None)
    if meta and meta.company_name:
        return meta.company_name
    return merchant.username


def _quantize_amount(value):
    if value is None:
        value = Decimal("0")
    elif not isinstance(value, Decimal):
        value = Decimal(value)
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _affiliate_company_metrics(user):
    start_of_month = timezone.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    creator_meta = getattr(user, "creatormeta", None)

    links = list(
        MerchantCreatorLink.objects.filter(creator=user)
        .select_related("merchant__merchantmeta")
        .order_by("merchant__username")
    )

    merchant_ids = [link.merchant_id for link in links if link.merchant_id]

    commission_entries = LedgerEntry.objects.filter(
        creator=user,
        entry_type="commission",
        merchant_id__in=merchant_ids,
    )

    totals_by_merchant = {
        row["merchant"]: row["total"]
        for row in commission_entries.values("merchant").annotate(total=Sum("amount"))
    }

    monthly_totals_by_merchant = {
        row["merchant"]: row["total"]
        for row in commission_entries.filter(timestamp__gte=start_of_month)
        .values("merchant")
        .annotate(total=Sum("amount"))
    }

    affiliate_clicks_by_store = {}
    if creator_meta:
        for row in (
            AffiliateClick.objects.filter(uuid=creator_meta.uuid)
            .values("storeID")
            .annotate(count=Count("id"))
        ):
            affiliate_clicks_by_store[str(row["storeID"])] = row["count"]

    visits_by_id = {}
    visits_by_uuid = {}
    for row in (
        ReferralVisit.objects.filter(creator=user)
        .values("merchant_id", "merchant_uuid")
        .annotate(count=Count("id"))
    ):
        merchant_id = row["merchant_id"]
        merchant_uuid = row["merchant_uuid"]
        if merchant_id:
            visits_by_id[merchant_id] = row["count"]
        if merchant_uuid:
            visits_by_uuid[str(merchant_uuid)] = row["count"]

    conversions_by_id = {}
    conversions_by_uuid = {}
    for row in (
        ReferralConversion.objects.filter(creator=user)
        .values("merchant_id", "merchant_uuid")
        .annotate(count=Count("id"))
    ):
        merchant_id = row["merchant_id"]
        merchant_uuid = row["merchant_uuid"]
        if merchant_id:
            conversions_by_id[merchant_id] = row["count"]
        if merchant_uuid:
            conversions_by_uuid[str(merchant_uuid)] = row["count"]

    active_companies = []
    inactive_companies = []
    pending_requests = []

    for link in links:
        merchant = link.merchant
        if merchant is None:
            continue

        merchant_meta = getattr(merchant, "merchantmeta", None)
        merchant_id = merchant.id

        total_earnings = _quantize_amount(totals_by_merchant.get(merchant_id))
        monthly_earnings = _quantize_amount(
            monthly_totals_by_merchant.get(merchant_id)
        )

        visits = None
        if merchant_meta:
            visits = affiliate_clicks_by_store.get(str(merchant_meta.uuid))
        if visits is None:
            visits = visits_by_id.get(merchant_id)
        if visits is None and merchant_meta:
            visits = visits_by_uuid.get(str(merchant_meta.uuid))
        visits = visits or 0

        conversions = conversions_by_id.get(merchant_id)
        if conversions is None and merchant_meta:
            conversions = conversions_by_uuid.get(str(merchant_meta.uuid))
        conversions = conversions or 0

        if visits:
            avg = _quantize_amount(total_earnings / Decimal(visits))
            conversion_rate = _quantize_amount(
                (Decimal(conversions) / Decimal(visits)) * Decimal("100")
            )
        else:
            avg = Decimal("0.00")
            conversion_rate = Decimal("0.00")

        entry = {
            "link_id": link.id,
            "merchant_id": merchant_id,
            "status": link.status,
            "business": _merchant_display_name(merchant),
            "email": merchant.email,
            "monthly_earnings": float(monthly_earnings),
            "total_earnings": float(total_earnings),
            "visits": visits,
            "conversions": conversions,
            "avg_per_visit": float(avg),
            "conversion_rate": float(conversion_rate),
        }

        if link.status == STATUS_ACTIVE:
            active_companies.append(entry)
        elif link.status == STATUS_INACTIVE:
            inactive_companies.append(entry)
        elif link.status == STATUS_REQUESTED:
            pending_requests.append(
                {
                    "id": link.id,
                    "name": _merchant_display_name(merchant),
                    "email": merchant.email,
                }
            )

    return {
        "active": active_companies,
        "inactive": inactive_companies,
        "pending_requests": pending_requests,
        "generated_at": timezone.now().isoformat(),
    }

def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _refresh_shopify_assets(items):
    items_by_merchant = {}
    for item in items:
        items_by_merchant.setdefault(item.merchant_id, []).append(item)

    for merchant_id, merchant_items in items_by_merchant.items():
        missing_items = [
            item
            for item in merchant_items
            if item.shopify_product_id and _is_blank(item.image_url)
        ]
        if not missing_items:
            continue

        meta = MerchantMeta.objects.filter(user_id=merchant_id).first()
        if not meta or not meta.shopify_access_token or not meta.shopify_store_domain:
            continue

        client = ShopifyClient(
            meta.shopify_access_token,
            meta.shopify_store_domain,
        )
        try:
            products = client.get_products_by_ids(
                [item.shopify_product_id for item in missing_items]
            )
        except Exception:
            logger.exception(
                "Failed to refresh Shopify images for merchant_id=%s.",
                merchant_id,
            )
            continue

        products_by_id = {str(product.get("id")): product for product in products}
        for item in missing_items:
            product = products_by_id.get(str(item.shopify_product_id), {})
            featured_image = ((product or {}).get("featuredImage") or {}).get("src")
            if not featured_image:
                images = (product or {}).get("images") or []
                if images:
                    featured_image = images[0].get("src")
            variants = (product or {}).get("variants") or []
            variant_price = variants[0].get("price") if variants else None
            update_fields = []
            if featured_image and item.image_url != featured_image:
                item.image_url = featured_image
                update_fields.append("image_url")
            if variant_price is not None and item.price != variant_price:
                item.price = variant_price
                update_fields.append("price")
            if not featured_image:
                logger.info(
                    "Shopify image URL missing for item_id=%s shopify_product_id=%s.",
                    item.id,
                    item.shopify_product_id,
                )
            if update_fields:
                item.save(update_fields=update_fields)


@login_required
def creator_affiliate_companies(request):
    metrics = _affiliate_company_metrics(request.user)

    return render(
        request,
        "creators/affiliate_companies.html",
        {
            "pending_requests": metrics["pending_requests"],
        },
    )


@login_required
def creator_affiliate_companies_data(request):
    metrics = _affiliate_company_metrics(request.user)
    return JsonResponse(metrics)


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
            item_queryset = (
                MerchantItem.objects.filter(groups__merchant_id__in=merchant_ids)
                .distinct()
                .prefetch_related("groups")
            )
            item_queryset = item_queryset.filter(
                Q(title__icontains=query)
                | Q(shopify_product_id__icontains=query)
                | Q(id__icontains=query)
            )

            item_list = list(item_queryset)
            _refresh_shopify_assets(item_list)
            merchant_ids_found = set()
            group_ids_found = set()
            items = []
            for item in item_list:
                commission_percent = None
                commission_amount = None
                first_group = item.groups.first()
                if first_group:
                    commission_percent = first_group.affiliate_percent
                    if item.price is not None:
                        try:
                            commission_amount = (
                                Decimal(str(item.price))
                                * Decimal(str(commission_percent))
                                / Decimal("100")
                            )
                        except (InvalidOperation, TypeError):
                            commission_amount = None
                merchant_ids_found.add(item.merchant_id)
                group_ids_found.update(item.groups.values_list("id", flat=True))
                base_link = item.link
                sep = "&" if "?" in base_link else "?"
                affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
                if item.shopify_product_id:
                    affiliate_link += f"&item_id={item.shopify_product_id}"
                items.append(
                    {
                        "item": item,
                        "affiliate_link": affiliate_link,
                        "commission_percent": commission_percent,
                        "commission_amount": commission_amount,
                    }
                )

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
            item_queryset = (
                MerchantItem.objects.filter(groups__merchant=merchant)
                .distinct()
                .prefetch_related("groups")
            )
            item_queryset = item_queryset.filter(
                Q(title__icontains=query)
                | Q(shopify_product_id__icontains=query)
                | Q(id__icontains=query)
            )
            item_list = list(item_queryset)
            _refresh_shopify_assets(item_list)
            items = []
            for item in item_list:
                commission_percent = None
                commission_amount = None
                first_group = item.groups.first()
                if first_group:
                    commission_percent = first_group.affiliate_percent
                    if item.price is not None:
                        try:
                            commission_amount = (
                                Decimal(str(item.price))
                                * Decimal(str(commission_percent))
                                / Decimal("100")
                            )
                        except (InvalidOperation, TypeError):
                            commission_amount = None
                base_link = item.link
                sep = "&" if "?" in base_link else "?"
                affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
                if item.shopify_product_id:
                    affiliate_link += f"&item_id={item.shopify_product_id}"
                items.append(
                    {
                        "item": item,
                        "affiliate_link": affiliate_link,
                        "commission_percent": commission_percent,
                        "commission_amount": commission_amount,
                    }
                )
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

    item_queryset = group.items.all().prefetch_related("groups")
    if query:
        item_queryset = item_queryset.filter(
            Q(title__icontains=query)
            | Q(shopify_product_id__icontains=query)
            | Q(id__icontains=query)
        )

    item_list = list(item_queryset)
    _refresh_shopify_assets(item_list)
    items = []
    for item in item_list:
        commission_percent = group.affiliate_percent
        commission_amount = None
        if item.price is not None:
            try:
                commission_amount = (
                    Decimal(str(item.price))
                    * Decimal(str(commission_percent))
                    / Decimal("100")
                )
            except (InvalidOperation, TypeError):
                commission_amount = None
        base_link = item.link
        sep = "&" if "?" in base_link else "?"
        affiliate_link = f"{base_link}{sep}ref=badger:{creator_meta.uuid}"
        if item.shopify_product_id:
            affiliate_link += f"&item_id={item.shopify_product_id}"
        items.append(
            {
                "item": item,
                "affiliate_link": affiliate_link,
                "commission_percent": commission_percent,
                "commission_amount": commission_amount,
            }
        )

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
