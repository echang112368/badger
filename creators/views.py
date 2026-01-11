from decimal import Decimal, InvalidOperation
import logging
import json
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db.models import Sum, Q, Count
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .models import CreatorMeta
from accounts.forms import UserNameForm
from collect.models import AffiliateClick, ReferralVisit, ReferralConversion

from links.models import (
    MerchantCreatorLink,
    PartnershipRequest,
    REQUEST_STATUS_ACCEPTED,
    REQUEST_STATUS_DECLINED,
    REQUEST_STATUS_PENDING,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_REQUESTED,
)
from merchants.models import MerchantMeta, ItemGroup, MerchantItem
from shopify_app.shopify_client import ShopifyClient
from shopify_app.token_management import refresh_shopify_token
from accounts.models import CustomUser
from ledger.models import LedgerEntry

logger = logging.getLogger(__name__)

def disp(message: str) -> None:
    print(message, flush=True)

@login_required
def creator_earnings(request):
    balance = LedgerEntry.creator_balance(request.user)
    entries = LedgerEntry.objects.filter(creator=request.user).order_by("-timestamp")
    commission_entries = LedgerEntry.objects.filter(
        creator=request.user,
        entry_type="commission",
    )
    monthly_data = (
        commission_entries
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
    last_30_days_start = timezone.now() - timedelta(days=30)
    total_earnings = commission_entries.aggregate(total=Sum("amount"))["total"] or 0
    last_30_days_earnings = (
        commission_entries.filter(timestamp__gte=last_30_days_start)
        .aggregate(total=Sum("amount"))["total"]
        or 0
    )
    return render(
        request,
        "creators/earnings.html",
        {
            "balance": balance,
            "ledger_entries": entries,
            "earnings_labels": earnings_labels,
            "earnings_totals": earnings_totals,
            "total_earnings": float(total_earnings),
            "last_30_days_earnings": float(last_30_days_earnings),
        },
    )


def _merchant_display_name(merchant):
    meta = getattr(merchant, "merchantmeta", None)
    if meta and meta.company_name:
        return meta.company_name
    return merchant.username


def _tokenize_query(query):
    tokens = [token for token in re.split(r"[\\s,]+", query.lower()) if token]
    expanded = set(tokens)
    for token in tokens:
        if token.endswith("s") and len(token) > 3:
            expanded.add(token.rstrip("s"))
    return list(expanded)


def _score_text(value, tokens, weight):
    if not value:
        return 0
    lowered = value.lower()
    return sum(weight for token in tokens if token in lowered)


def _merchant_store_url(meta):
    domain = (meta.shopify_store_domain or "").strip()
    if not domain:
        return ""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


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


@login_required
def creator_marketplace(request):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        creator_meta.marketplace_enabled = bool(
            request.POST.get("marketplace_enabled")
        )
        creator_meta.save(update_fields=["marketplace_enabled"])
        return redirect("creator_marketplace")

    query = (request.GET.get("q") or "").strip()
    affiliate_min_raw = (request.GET.get("affiliate_min") or "").strip()
    affiliate_max_raw = (request.GET.get("affiliate_max") or "").strip()
    business_type = (request.GET.get("business_type") or "").strip()
    search_scope = (request.GET.get("search_scope") or "all").strip().lower()
    if search_scope not in {"all", "business", "item"}:
        search_scope = "all"

    def _parse_decimal(value):
        if not value:
            return None
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return None

    affiliate_min = _parse_decimal(affiliate_min_raw)
    affiliate_max = _parse_decimal(affiliate_max_raw)
    merchant_cards = []
    item_cards = []

    if creator_meta.marketplace_enabled:
        connected_merchant_ids = list(
            MerchantCreatorLink.objects.filter(
                creator=request.user,
                status=STATUS_ACTIVE,
            ).values_list("merchant_id", flat=True)
        )
        merchant_qs = (
            MerchantMeta.objects.select_related("user")
            .filter(marketplace_enabled=True)
            .exclude(user_id__in=connected_merchant_ids)
            .order_by("company_name", "user__username")
        )
        if business_type:
            merchant_qs = merchant_qs.filter(business_type=business_type)
        if affiliate_min is not None:
            merchant_qs = merchant_qs.filter(
                user__itemgroup__affiliate_percent__gte=affiliate_min
            )
        if affiliate_max is not None:
            merchant_qs = merchant_qs.filter(
                user__itemgroup__affiliate_percent__lte=affiliate_max
            )
        merchant_qs = merchant_qs.distinct()
        merchant_metas = list(merchant_qs)
        merchant_users = [meta.user for meta in merchant_metas]
        groups_by_merchant = {}
        items_by_merchant = {}
        for group in (
            ItemGroup.objects.filter(merchant__in=merchant_users)
            .prefetch_related("items")
            .order_by("name")
        ):
            groups_by_merchant.setdefault(group.merchant_id, []).append(group)
        for item in (
            MerchantItem.objects.filter(merchant__in=merchant_users)
            .prefetch_related("groups")
            .order_by("title")
        ):
            items_by_merchant.setdefault(item.merchant_id, []).append(item)

        tokens = _tokenize_query(query) if query else []

        for meta in merchant_metas:
            groups = groups_by_merchant.get(meta.user_id, [])
            items = items_by_merchant.get(meta.user_id, [])
            if affiliate_min is not None:
                groups = [
                    group for group in groups if group.affiliate_percent >= affiliate_min
                ]
            if affiliate_max is not None:
                groups = [
                    group for group in groups if group.affiliate_percent <= affiliate_max
                ]
            commissions = [group.affiliate_percent for group in groups]
            commission_range = None
            if commissions:
                commission_range = {
                    "min": min(commissions),
                    "max": max(commissions),
                }

            score = 0
            if tokens:
                score += _score_text(meta.company_name or "", tokens, 4)
                score += _score_text(meta.shopify_store_domain or "", tokens, 3)
                score += _score_text(meta.user.username or "", tokens, 2)
                for group in groups:
                    score += _score_text(group.name or "", tokens, 2)
                for item in items:
                    score += _score_text(item.title or "", tokens, 1)

            if tokens and score == 0:
                continue

            merchant_cards.append(
                {
                    "meta": meta,
                    "display_name": _merchant_display_name(meta.user),
                    "groups": groups,
                    "commission_range": commission_range,
                    "store_url": _merchant_store_url(meta),
                    "score": score,
                }
            )

        for meta in merchant_metas:
            store_url = _merchant_store_url(meta)
            for group in groups_by_merchant.get(meta.user_id, []):
                if affiliate_min is not None and group.affiliate_percent < affiliate_min:
                    continue
                if affiliate_max is not None and group.affiliate_percent > affiliate_max:
                    continue
                score = 0
                if tokens:
                    score += _score_text(group.name or "", tokens, 4)
                    score += _score_text(meta.company_name or "", tokens, 2)
                    score += _score_text(meta.shopify_store_domain or "", tokens, 2)
                if tokens and score == 0:
                    continue
                image_url = None
                for item in group.items.all():
                    if item.image_url:
                        image_url = item.image_url
                        break
                item_cards.append(
                    {
                        "type": "Collection",
                        "name": group.name,
                        "merchant": meta,
                        "merchant_name": _merchant_display_name(meta.user),
                        "merchant_url": store_url,
                        "image_url": image_url,
                        "commission": group.affiliate_percent,
                        "item_group_id": group.id,
                        "item_id": None,
                        "detail_url": store_url,
                        "score": score,
                    }
                )

            for item in items_by_merchant.get(meta.user_id, []):
                group_commissions = [group.affiliate_percent for group in item.groups.all()]
                commission = max(group_commissions) if group_commissions else None
                if affiliate_min is not None and commission is not None and commission < affiliate_min:
                    continue
                if affiliate_max is not None and commission is not None and commission > affiliate_max:
                    continue
                score = 0
                if tokens:
                    score += _score_text(item.title or "", tokens, 5)
                    score += _score_text(meta.company_name or "", tokens, 2)
                    score += _score_text(meta.shopify_store_domain or "", tokens, 2)
                    for group in item.groups.all():
                        score += _score_text(group.name or "", tokens, 1)
                if tokens and score == 0:
                    continue
                item_cards.append(
                    {
                        "type": "Product",
                        "name": item.title,
                        "merchant": meta,
                        "merchant_name": _merchant_display_name(meta.user),
                        "merchant_url": store_url,
                        "image_url": item.image_url,
                        "commission": commission,
                        "item_id": item.id,
                        "item_group_id": None,
                        "detail_url": item.link,
                        "score": score,
                    }
                )

    merchant_cards.sort(key=lambda card: (-card["score"], card["display_name"].lower()))
    item_cards.sort(key=lambda card: (-card["score"], card["name"].lower()))

    if search_scope == "all":
        merchant_cards_display = merchant_cards[:6]
        item_cards_display = item_cards[:6]
    elif search_scope == "business":
        merchant_cards_display = merchant_cards
        item_cards_display = []
    else:
        merchant_cards_display = []
        item_cards_display = item_cards

    pending_requests = PartnershipRequest.objects.filter(
        creator=request.user,
        status=REQUEST_STATUS_PENDING,
    )
    requested_merchants = set()
    requested_items = set()
    requested_item_groups = set()
    for pending in pending_requests:
        requested_merchants.add(pending.merchant_id)
        if pending.item_id:
            requested_items.add(pending.item_id)
        if pending.item_group_id:
            requested_item_groups.add(pending.item_group_id)

    return render(
        request,
        "creators/marketplace.html",
        {
            "creator_meta": creator_meta,
            "merchant_cards": merchant_cards,
            "merchant_cards_display": merchant_cards_display,
            "item_cards": item_cards,
            "item_cards_display": item_cards_display,
            "query": query,
            "affiliate_min": affiliate_min_raw,
            "affiliate_max": affiliate_max_raw,
            "business_type": business_type,
            "business_types": MerchantMeta.BusinessType,
            "search_scope": search_scope,
            "requested_merchants": list(requested_merchants),
            "requested_items": list(requested_items),
            "requested_item_groups": list(requested_item_groups),
        },
    )

def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


@login_required
def creator_send_request(request):
    if request.method != "POST" or not request.user.is_creator:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            logger.warning(
                "creator_request_submit_failed_unauthorized user_id=%s",
                getattr(request.user, "id", None),
            )
            return JsonResponse(
                {"status": "error", "message": "You must be logged in as a creator."},
                status=403,
            )
        return redirect("creator_marketplace")

    merchant_id = request.POST.get("merchant_id")
    logger.info(
        "creator_request_submit_start creator_id=%s merchant_id=%s item_id=%s item_group_id=%s",
        request.user.id,
        merchant_id,
        request.POST.get("item_id"),
        request.POST.get("item_group_id"),
    )
    if not merchant_id:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            logger.warning(
                "creator_request_submit_failed_missing_merchant creator_id=%s",
                request.user.id,
            )
            return JsonResponse({"status": "error", "message": "Missing merchant."}, status=400)
        return redirect("creator_marketplace")
    item_id = request.POST.get("item_id") or None
    item_group_id = request.POST.get("item_group_id") or None
    message = (request.POST.get("message") or "").strip()

    merchant = get_object_or_404(CustomUser, id=merchant_id, is_merchant=True)
    merchant_meta = getattr(merchant, "merchantmeta", None)
    if not merchant_meta or not merchant_meta.marketplace_enabled:
        logger.warning(
            "creator_request_submit_failed_marketplace_disabled creator_id=%s merchant_id=%s",
            request.user.id,
            merchant_id,
        )
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "status": "error",
                    "message": "This business is not accepting requests right now.",
                },
                status=400,
            )
        return redirect("creator_marketplace")
    item = None
    item_group = None
    if item_id:
        item = get_object_or_404(MerchantItem, id=item_id, merchant=merchant)
    if item_group_id:
        item_group = get_object_or_404(ItemGroup, id=item_group_id, merchant=merchant)

    if MerchantCreatorLink.objects.filter(
        merchant=merchant, creator=request.user, status=STATUS_ACTIVE
    ).exists():
        logger.info(
            "creator_request_submit_skipped_active_link creator_id=%s merchant_id=%s",
            request.user.id,
            merchant_id,
        )
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "status": "error",
                    "message": "You already have an active partnership with this business.",
                },
                status=400,
            )
        return redirect("creator_requests")

    partnership_request, created = PartnershipRequest.objects.get_or_create(
        creator=request.user,
        merchant=merchant,
        item=item,
        item_group=item_group,
        defaults={
            "message": message,
            "status": REQUEST_STATUS_PENDING,
        },
    )
    if not created and partnership_request.status != REQUEST_STATUS_PENDING:
        partnership_request.status = REQUEST_STATUS_PENDING
        partnership_request.message = message
        partnership_request.save(update_fields=["status", "message", "updated_at"])

    link, created = MerchantCreatorLink.objects.get_or_create(
        merchant=merchant,
        creator=request.user,
        defaults={"status": STATUS_REQUESTED},
    )
    if not created and link.status != STATUS_ACTIVE:
        link.status = STATUS_REQUESTED
        link.save(update_fields=["status"])

    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    disp(
        "creator_request_success "
        f"creator={request.user.username} "
        f"creator_uuid={creator_meta.uuid} "
        f"merchant={_merchant_display_name(merchant)} "
        f"merchant_uuid={merchant_meta.uuid}"
    )
    logger.info(
        "creator_request_submit_success creator_id=%s merchant_id=%s request_id=%s",
        request.user.id,
        merchant_id,
        partnership_request.id,
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"status": "ok"})
    return redirect("creator_marketplace")


@login_required
def creator_requests(request):
    if not request.user.is_creator:
        return redirect("creator_marketplace")

    requests = (
        PartnershipRequest.objects.filter(creator=request.user)
        .select_related("merchant", "merchant__merchantmeta", "item", "item_group")
        .order_by("-created_at")
    )
    grouped = {
        REQUEST_STATUS_PENDING: [],
        REQUEST_STATUS_ACCEPTED: [],
        REQUEST_STATUS_DECLINED: [],
    }

    for req in requests:
        meta = getattr(req.merchant, "merchantmeta", None)
        grouped[req.status].append(
            {
                "id": req.id,
                "merchant_name": _merchant_display_name(req.merchant),
                "business_type": meta.get_business_type_display() if meta else "Merchant",
                "item_name": req.item.title if req.item else (req.item_group.name if req.item_group else None),
                "status": req.status,
                "created_at": req.created_at,
                "message": req.message,
            }
        )

    return render(
        request,
        "creators/requests.html",
        {
            "requests_grouped": grouped,
        },
    )


SOCIAL_PLATFORM_OPTIONS = [
    "YouTube",
    "TikTok",
    "Instagram",
    "Snapchat",
    "Twitter / X",
    "Facebook",
    "Twitch",
    "Discord",
    "Reddit",
    "Pinterest",
    "LinkedIn",
    "Blog / Website",
    "Newsletter (Substack, Beehiiv, etc.)",
    "Podcast",
    "Other",
]

FOLLOWER_RANGE_OPTIONS = [
    "0–1k",
    "1k–5k",
    "5k–10k",
    "10k–50k",
    "50k–100k",
    "100k–500k",
    "500k–1M",
    "1M–2M",
    "2M–3M",
    "3M–4M",
    "4M–5M",
    "5M–6M",
    "6M–7M",
    "7M–8M",
    "8M–9M",
    "9M–10M",
    "10M+",
]


def _normalize_short_pitch(value, max_length=240):
    if value is None:
        return ""
    pitch = " ".join(value.strip().split())
    return pitch[:max_length]


def _parse_content_skills(raw_value, max_skills=20):
    if not raw_value:
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    skills = []
    seen = set()
    for item in payload:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.strip().split())
        if not cleaned:
            continue
        if len(cleaned.split()) > 3:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        skills.append(cleaned)
        seen.add(key)
        if len(skills) >= max_skills:
            break
    return skills


def _parse_social_media_profiles(post_data):
    platforms = post_data.getlist("platform")
    follower_ranges = post_data.getlist("platform_follower_range")
    profile_urls = post_data.getlist("platform_url")
    custom_platforms = post_data.getlist("platform_custom")

    total = max(
        len(platforms),
        len(follower_ranges),
        len(profile_urls),
        len(custom_platforms),
    )
    profiles = []
    for idx in range(total):
        platform = platforms[idx].strip() if idx < len(platforms) else ""
        follower_range = (
            follower_ranges[idx].strip() if idx < len(follower_ranges) else ""
        )
        profile_url = profile_urls[idx].strip() if idx < len(profile_urls) else ""
        custom_platform = (
            custom_platforms[idx].strip() if idx < len(custom_platforms) else ""
        )
        platform_name = custom_platform if platform == "Other" else platform
        if not (platform_name or follower_range or profile_url):
            continue
        profiles.append(
            {
                "platform": platform_name,
                "follower_range": follower_range,
                "profile_url": profile_url,
            }
        )
    return profiles


def _prepare_social_media_profiles(profiles):
    platform_set = set(SOCIAL_PLATFORM_OPTIONS)
    prepared = []
    for profile in profiles or []:
        platform = (profile.get("platform") or "").strip()
        follower_range = (profile.get("follower_range") or "").strip()
        profile_url = (profile.get("profile_url") or "").strip()
        if not (platform or follower_range or profile_url):
            continue
        platform_value = platform if platform in platform_set else "Other" if platform else ""
        custom_platform = "" if platform in platform_set else platform
        prepared.append(
            {
                "platform": platform,
                "platform_value": platform_value,
                "custom_platform": custom_platform,
                "follower_range": follower_range,
                "profile_url": profile_url,
            }
        )
    return prepared


def _refresh_shopify_assets(items):
    items_by_merchant = {}
    for item in items:
        items_by_merchant.setdefault(item.merchant_id, []).append(item)

    for merchant_id, merchant_items in items_by_merchant.items():
        message = (
            f"Refreshing Shopify assets for merchant_id={merchant_id} "
            f"(items={len(merchant_items)})."
        )
        logger.info(message)
        print(message)
        missing_items = [
            item
            for item in merchant_items
            if item.shopify_product_id and _is_blank(item.image_url)
        ]
        message = (
            f"Detected {len(missing_items)} items without image URLs for "
            f"merchant_id={merchant_id}; attempting refresh."
        )
        logger.info(message)
        print(message)
        if not missing_items:
            continue

        meta = MerchantMeta.objects.filter(user_id=merchant_id).first()
        if not meta or not meta.shopify_access_token or not meta.shopify_store_domain:
            logger.info(
                "Skipping Shopify refresh for merchant_id=%s due to missing credentials.",
                merchant_id,
            )
            print(
                f"Skipping Shopify refresh for merchant_id={merchant_id} due to missing credentials."
            )
            continue

        client = ShopifyClient(
            meta.shopify_access_token,
            meta.shopify_store_domain,
            refresh_handler=lambda: refresh_shopify_token(meta),
            token_type="offline",
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
        message = (
            f"Attempted to pull images again for merchant_id={merchant_id} "
            f"(items={len(missing_items)})."
        )
        logger.info(message)
        print(message)

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
                print(
                    "Shopify image URL missing for "
                    f"item_id={item.id} shopify_product_id={item.shopify_product_id}."
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
        paypal_email = request.POST.get("paypal_email", "").strip()
        creator_meta.paypal_email = paypal_email
        creator_meta.save()
        return redirect("creator_settings")

    return render(
        request,
        "creators/settings.html",
        {"creator_meta": creator_meta, "creator": request.user},
    )


@login_required
def creator_profile(request):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        user_form = UserNameForm(request.POST, instance=request.user)
        email = request.POST.get("email", "").strip()
        short_pitch = _normalize_short_pitch(request.POST.get("short_pitch", ""))
        country = request.POST.get("country", "").strip()
        content_languages = request.POST.get("content_languages", "").strip()
        social_media_profiles = _parse_social_media_profiles(request.POST)
        content_skills = _parse_content_skills(request.POST.get("content_skills"))
        if user_form.is_valid():
            user = user_form.save(commit=False)
            if email:
                user.email = email
            user.save()
            creator_meta.short_pitch = short_pitch
            creator_meta.country = country
            creator_meta.content_languages = content_languages
            creator_meta.social_media_profiles = social_media_profiles
            creator_meta.content_skills = content_skills
            creator_meta.save()
            return redirect("creator_profile")
    else:
        user_form = UserNameForm(instance=request.user)

    return render(
        request,
        "creators/profile.html",
        {
            "creator_meta": creator_meta,
            "creator": request.user,
            "user_form": user_form,
            "creator_avatar": {
                "name": request.user.get_full_name() or request.user.username,
                "initials": "".join(
                    part[0]
                    for part in (request.user.get_full_name() or request.user.username).split()
                    if part
                )[:2].upper()
                or "CR",
                "avatar_url": creator_meta.primary_platform_data()[2],
            },
            "social_media_profiles": _prepare_social_media_profiles(
                creator_meta.social_media_profiles
            ),
            "content_skills": creator_meta.content_skills or [],
            "social_platform_options": SOCIAL_PLATFORM_OPTIONS,
            "follower_range_options": FOLLOWER_RANGE_OPTIONS,
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
