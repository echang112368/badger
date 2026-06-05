from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging
import json
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.db import close_old_connections
from django.db.models import Sum, Q, Count
from django.utils import timezone
from .models import CreatorMeta
from .models import PartnerMessage
from .models import SocialAnalyticsSnapshot
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
from .services.social_dashboard import SocialDashboardService
from instagram_connect.models import InstagramConnection
from agent.models import Conversation
from .services.dashboard import build_creator_dashboard_context
from .services.ai_profile_feedback import refresh_ai_score_if_stale
from .services.gmail_oauth import (
    GmailOAuthError,
    build_gmail_authorization_url,
    exchange_gmail_callback,
    get_gmail_connection_status,
    revoke_gmail_connection,
)

import threading
import uuid

logger = logging.getLogger(__name__)

_SOCIAL_REFRESH_JOBS: dict[str, dict] = {}
_SOCIAL_REFRESH_JOBS_LOCK = threading.Lock()
_SOCIAL_REFRESH_PLATFORM_LABELS = {
    SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM: "Instagram",
}


def _set_social_refresh_job(job_id: str, **updates) -> dict:
    with _SOCIAL_REFRESH_JOBS_LOCK:
        job = _SOCIAL_REFRESH_JOBS.get(job_id, {})
        job.update(updates)
        _SOCIAL_REFRESH_JOBS[job_id] = job
        return dict(job)


def _get_social_refresh_job(job_id: str, user_id: int | None = None) -> dict | None:
    with _SOCIAL_REFRESH_JOBS_LOCK:
        job = _SOCIAL_REFRESH_JOBS.get(job_id)
        if not job:
            return None
        if user_id is not None and job.get("user_id") != user_id:
            return None
        return dict(job)


def _run_social_refresh_job(job_id: str, user_id: int, platform: str, reanalyze: bool) -> None:
    platform_label = _SOCIAL_REFRESH_PLATFORM_LABELS.get(platform, platform.title())
    _set_social_refresh_job(
        job_id,
        status="running",
        step="fetching_instagram",
        message=f"Fetching latest {platform_label} profile, audience, and post insights...",
        started_at=timezone.now().isoformat(),
    )
    try:
        close_old_connections()
        user = CustomUser.objects.get(pk=user_id)
        service = SocialDashboardService(user)
        _set_social_refresh_job(
            job_id,
            status="running",
            step="refreshing_metrics",
            message="Crunching engagement, audience, and content performance metrics...",
        )
        service.build_dashboard(
            refresh_platform=platform,
            force_reanalyze=reanalyze,
            allow_auto_refresh=True,
            allow_ai_refresh=True,
        )
        _set_social_refresh_job(
            job_id,
            status="complete",
            step="complete",
            message="Social analytics are ready.",
            finished_at=timezone.now().isoformat(),
        )
    except Exception as exc:
        logger.exception(
            "Social media refresh failed",
            extra={"user_id": user_id, "platform": platform, "job_id": job_id},
        )
        _set_social_refresh_job(
            job_id,
            status="failed",
            step="failed",
            message="We could not refresh social analytics right now. Cached data is still available.",
            error=str(exc),
            finished_at=timezone.now().isoformat(),
        )
    finally:
        close_old_connections()


def _start_social_refresh_job(user, platform: str, reanalyze: bool = False) -> str:
    with _SOCIAL_REFRESH_JOBS_LOCK:
        for existing_job_id, existing_job in _SOCIAL_REFRESH_JOBS.items():
            if (
                existing_job.get("user_id") == user.id
                and existing_job.get("platform") == platform
                and existing_job.get("status") in {"queued", "running"}
                and (not reanalyze or existing_job.get("reanalyze"))
            ):
                return existing_job_id

        job_id = uuid.uuid4().hex
        _SOCIAL_REFRESH_JOBS[job_id] = {
            "job_id": job_id,
            "user_id": user.id,
            "platform": platform,
            "reanalyze": reanalyze,
            "status": "queued",
            "step": "queued",
            "message": "Preparing social analytics refresh...",
            "requested_at": timezone.now().isoformat(),
        }

    thread = threading.Thread(
        target=_run_social_refresh_job,
        args=(job_id, user.id, platform, reanalyze),
        daemon=True,
    )
    thread.start()
    return job_id


def _build_loading_social_dashboard(user, platform: str | None = None) -> dict:
    connection = getattr(user, "instagram_connection", None)
    if connection is None:
        connection = InstagramConnection.objects.filter(user=user).first()

    instagram_connected = bool(connection)
    platforms = [
        {
            "slug": SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
            "name": "Instagram",
            "connected": instagram_connected,
            "can_connect": True,
            "connect_url": "/instagram/connect/",
            "refreshed": False,
            "last_synced_at": getattr(connection, "last_synced_at", None),
            "metrics": {
                "account": {
                    "username": getattr(connection, "instagram_username", "") or "connected",
                    "followers_count": getattr(connection, "followers_count", 0) or 0,
                    "media_count": getattr(connection, "media_count", 0) or 0,
                }
            },
        },
        {
            "slug": "tiktok",
            "name": "TikTok",
            "connected": False,
            "can_connect": False,
            "connect_url": "#",
            "refreshed": False,
            "last_synced_at": None,
            "metrics": {},
        },
        {
            "slug": "youtube",
            "name": "YouTube",
            "connected": False,
            "can_connect": False,
            "connect_url": "#",
            "refreshed": False,
            "last_synced_at": None,
            "metrics": {},
        },
    ]
    return {
        "overall": {
            "connected_platforms": 1 if instagram_connected else 0,
            "total_followers": getattr(connection, "followers_count", 0) or 0,
            "total_reach": 0,
            "average_engagement_rate": 0,
            "top_platform": "Instagram" if instagram_connected else "None",
        },
        "platforms": platforms,
    }


def _trigger_ai_refresh(user_id: int) -> None:
    """Fire-and-forget: refresh the AI score in a background thread."""
    t = threading.Thread(
        target=refresh_ai_score_if_stale,
        args=(user_id,),
        daemon=True,
    )
    t.start()

def disp(message: str) -> None:
    print(message, flush=True)

@login_required
def creator_dashboard(request):
    context = build_creator_dashboard_context(request.user)
    return render(request, "creators/dashboard.html", context)


@login_required
def creator_agent(request):
    connected_accounts = InstagramConnection.objects.filter(user=request.user)
    return render(request, "creators/agent.html", {
        "agent_connected_accounts": connected_accounts,
    })


@login_required
def gmail_connect(request):
    try:
        authorization_url = build_gmail_authorization_url(request)
    except GmailOAuthError as exc:
        messages.error(request, exc.user_message)
        return redirect("creator_settings")
    return redirect(authorization_url)


@login_required
def gmail_callback(request):
    try:
        credential = exchange_gmail_callback(request)
    except GmailOAuthError as exc:
        messages.error(request, exc.user_message)
    else:
        if credential.gmail_email:
            messages.success(request, f"Gmail connected for {credential.gmail_email}.")
        else:
            messages.success(request, "Gmail connected. Badger could not confirm the Gmail address yet.")
    return redirect("creator_settings")


@login_required
@require_POST
def gmail_disconnect(request):
    revoke_gmail_connection(request.user)
    status_payload = get_gmail_connection_status(request.user)
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", ""):
        return JsonResponse(status_payload)
    messages.success(request, "Gmail disconnected.")
    return redirect("creator_settings")


@login_required
@require_GET
def gmail_status(request):
    return JsonResponse(get_gmail_connection_status(request.user))


@login_required
def creator_earnings(request):
    entries = LedgerEntry.objects.filter(creator=request.user).order_by("-timestamp")
    dashboard_context = build_creator_dashboard_context(request.user)
    earnings = dashboard_context["earnings"]
    return render(
        request,
        "creators/earnings.html",
        {
            "balance": earnings.pending,
            "ledger_entries": entries,
            "earnings_labels": earnings.labels,
            "earnings_totals": earnings.totals,
            "total_earnings": float(earnings.total),
            "last_30_days_earnings": float(earnings.last_30_days),
            "performance": dashboard_context["performance"],
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
        lines = (req.message or "").splitlines()
        campaign_type = req.campaign_type or ""
        deal_type = req.deal_type or ""
        clean_message_lines = []
        for line in lines:
            if line.startswith("Campaign type:") and not campaign_type:
                campaign_type = line.replace("Campaign type:", "").strip()
            elif line.startswith("Deal type:") and not deal_type:
                deal_type = line.replace("Deal type:", "").strip()
            else:
                clean_message_lines.append(line)
        grouped[req.status].append(
            {
                "id": req.id,
                "merchant_name": _merchant_display_name(req.merchant),
                "business_type": meta.get_business_type_display() if meta else "Merchant",
                "item_name": req.item.title if req.item else (req.item_group.name if req.item_group else None),
                "status": req.status,
                "created_at": req.created_at,
                "message": "\n".join(clean_message_lines).strip(),
                "campaign_type": campaign_type,
                "deal_type": deal_type,
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


def _parse_niches(raw_value, max_niches=20, max_tag_length=50):
    if not raw_value:
        return []
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    niches = []
    seen = set()
    for item in payload:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.strip().split())
        if not cleaned or len(cleaned) > max_tag_length:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        niches.append(cleaned)
        seen.add(key)
        if len(niches) >= max_niches:
            break
    return niches


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
    links = list(
        MerchantCreatorLink.objects.filter(creator=request.user)
        .select_related("merchant__merchantmeta", "merchant__creator_preferences")
        .order_by("merchant__username")
    )
    requests_rows, active_rows, archived_rows = [], [], []
    palette = ["#4f46e5", "#0891b2", "#059669", "#d97706", "#7c3aed", "#be185d"]
    for idx, link in enumerate(links):
        merchant = link.merchant
        name = _merchant_display_name(merchant)
        initials = "".join([p[0] for p in name.split() if p][:2]).upper() or "BR"
        req = (
            PartnershipRequest.objects.filter(creator=request.user, merchant=merchant)
            .order_by("-created_at")
            .first()
        )
        last_message = link.messages.select_related("sender").order_by("-created_at").first()
        preview = (last_message.content if last_message else (req.message if req else "No messages yet")).strip()
        preview = preview[:80] + ("..." if len(preview) > 80 else "")
        prefs = getattr(merchant, "creator_preferences", None)
        row = {
            "link_id": link.id,
            "merchant_name": name,
            "email": merchant.email,
            "initials": initials,
            "avatar_color": palette[idx % len(palette)],
            "preview": preview,
            "timestamp": (last_message.created_at if last_message else (req.updated_at if req else None)),
            "status": link.status,
            "request_status": req.status if req else None,
            "creator_has_replied": req.creator_has_replied if req else True,
            "match_score": 86,
            "campaign_type": prefs.get_campaign_goal_display() if prefs and prefs.campaign_goal else "General",
            "partner_since": req.created_at.date().isoformat() if req else "",
            "niche": prefs.brand_description[:32] if prefs and prefs.brand_description else "General",
            "deal_type": prefs.budget_range if prefs and prefs.budget_range else "Flexible",
            "opening_message": req.message if req else "",
            "has_unread": False,
        }
        has_pending_request = req and req.status == REQUEST_STATUS_PENDING and not req.creator_has_replied
        if link.status == STATUS_REQUESTED or has_pending_request:
            requests_rows.append(row)
        elif link.status == STATUS_ACTIVE:
            active_rows.append(row)
        else:
            archived_rows.append(row)

    return render(
        request,
        "creators/affiliate_companies.html",
        {
            "requests_rows": requests_rows,
            "active_rows": active_rows,
            "archived_rows": archived_rows,
        },
    )


@login_required
def creator_affiliate_companies_data(request):
    metrics = _affiliate_company_metrics(request.user)
    return JsonResponse(metrics)


@login_required
def creator_partner_messages(request, link_id):
    link = get_object_or_404(MerchantCreatorLink, id=link_id, creator=request.user)
    if request.method == "POST":
        content = (request.POST.get("content") or "").strip()
        if not content:
            return JsonResponse({"status": "error", "message": "Message cannot be empty."}, status=400)
        message = PartnerMessage.objects.create(
            partnership=link,
            sender=request.user,
            content=content,
        )
        pr = PartnershipRequest.objects.filter(merchant=link.merchant, creator=request.user).order_by('-created_at').first()
        if pr:
            update_fields = ['last_message_at', 'updated_at']
            pr.last_message_at = timezone.now()
            if not pr.creator_has_replied:
                pr.creator_has_replied = True
                pr.thread_unlocked_at = timezone.now()
                update_fields += ['creator_has_replied', 'thread_unlocked_at']
            pr.save(update_fields=update_fields)
        return JsonResponse({
            "status": "ok",
            "message": {
                "id": message.id,
                "content": message.content,
                "sender_id": message.sender_id,
                "created_at": message.created_at.isoformat(),
                "is_opening_message": False,
            },
        })

    messages = list(
        link.messages.select_related("sender").values("id", "sender_id", "content", "created_at", "is_opening_message")
    )
    for message in messages:
        message["created_at"] = message["created_at"].isoformat()
    pr = PartnershipRequest.objects.filter(merchant=link.merchant, creator=request.user).order_by('-created_at').first()
    return JsonResponse({
        "status": "ok",
        "creator_has_replied": pr.creator_has_replied if pr else True,
        "messages": messages,
    })


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

    if request.method == "GET":
        try:
            SocialDashboardService(request.user).refresh_stale_platforms()
        except Exception:
            logger.warning(
                "Unable to auto-resync social analytics for settings page",
                exc_info=True,
                extra={"user_id": request.user.id},
            )

    instagram_connection = getattr(request.user, "instagram_connection", None)

    return render(
        request,
        "creators/settings.html",
        {
            "creator_meta": creator_meta,
            "creator": request.user,
            "instagram_connection": instagram_connection,
            "gmail_status": get_gmail_connection_status(request.user),
        },
    )


@login_required
def creator_profile(request):
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        def _safe_non_negative_int(value):
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        def _safe_non_negative_float(value):
            try:
                return max(0.0, float(value or 0))
            except (TypeError, ValueError):
                return 0.0

        user_form = UserNameForm(request.POST, instance=request.user)
        email = request.POST.get("email", "").strip()
        short_pitch = _normalize_short_pitch(request.POST.get("short_pitch", ""))
        country = request.POST.get("country", "").strip()
        content_languages = request.POST.get("content_languages", "").strip()
        social_media_profiles = _parse_social_media_profiles(request.POST)
        content_skills = _parse_content_skills(request.POST.get("content_skills"))
        niches = _parse_niches(request.POST.get("niches"))
        paid_brand_deals_count = _safe_non_negative_int(request.POST.get("paid_brand_deals_count", "0"))
        gifted_brand_deals_count = _safe_non_negative_int(request.POST.get("gifted_brand_deals_count", "0"))
        affiliate_brand_deals_count = _safe_non_negative_int(request.POST.get("affiliate_brand_deals_count", "0"))
        avg_sponsored_conversion_rate_pct = _safe_non_negative_float(request.POST.get("avg_sponsored_conversion_rate_pct", "0"))
        partnership_history_notes = request.POST.get("partnership_history_notes", "").strip()
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
            creator_meta.niches = niches
            creator_meta.paid_brand_deals_count = paid_brand_deals_count
            creator_meta.gifted_brand_deals_count = gifted_brand_deals_count
            creator_meta.affiliate_brand_deals_count = affiliate_brand_deals_count
            creator_meta.avg_sponsored_conversion_rate_pct = avg_sponsored_conversion_rate_pct
            creator_meta.partnership_history_notes = partnership_history_notes
            creator_meta.save()
            _trigger_ai_refresh(request.user.pk)
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
            "niches": creator_meta.niches or [],
            "niches_json": json.dumps(creator_meta.niches or []),
            "social_platform_options": SOCIAL_PLATFORM_OPTIONS,
            "follower_range_options": FOLLOWER_RANGE_OPTIONS,
        },
    )


@login_required
def creator_support(request):
    return render(request, "creators/support.html")


@login_required
def creator_social_media(request):
    refresh_platform = request.GET.get("refresh")
    if refresh_platform and refresh_platform not in {SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM}:
        refresh_platform = None
    force_reanalyze = bool(request.GET.get("reanalyze"))
    service = SocialDashboardService(request.user)
    should_show_loading = bool(refresh_platform) or service.needs_refresh()

    if should_show_loading:
        platform = refresh_platform or SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM
        job_id = _start_social_refresh_job(
            request.user,
            platform=platform,
            reanalyze=force_reanalyze,
        )
        dashboard = _build_loading_social_dashboard(request.user, platform=platform)
        return render(
            request,
            "creators/social_media.html",
            {
                "social_overall": dashboard["overall"],
                "social_platforms": dashboard["platforms"],
                "refreshed_platform": refresh_platform,
                "social_loading": True,
                "social_refresh_job_id": job_id,
                "social_refresh_status_url": reverse("creator_social_media_refresh_status"),
                "social_refresh_start_url": reverse("creator_social_media_refresh_start"),
            },
        )

    dashboard = service.build_dashboard(
        refresh_platform=None,
        force_reanalyze=False,
        allow_auto_refresh=False,
        allow_ai_refresh=False,
    )
    return render(
        request,
        "creators/social_media.html",
        {
            "social_overall": dashboard["overall"],
            "social_platforms": dashboard["platforms"],
            "refreshed_platform": refresh_platform,
            "social_loading": False,
            "social_refresh_status_url": reverse("creator_social_media_refresh_status"),
            "social_refresh_start_url": reverse("creator_social_media_refresh_start"),
        },
    )


@login_required
@require_POST
def creator_social_media_refresh_start(request):
    platform = (request.POST.get("platform") or SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM).strip()
    if platform not in {SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM}:
        return JsonResponse({"error": "Unsupported platform."}, status=400)

    reanalyze = request.POST.get("reanalyze") in {"1", "true", "True", "yes", "on"}
    job_id = _start_social_refresh_job(request.user, platform=platform, reanalyze=reanalyze)
    job = _get_social_refresh_job(job_id, request.user.id) or {}
    return JsonResponse(
        {
            "job_id": job_id,
            "status": job.get("status", "queued"),
            "step": job.get("step", "queued"),
            "message": job.get("message", "Preparing social analytics refresh..."),
        }
    )


@login_required
@require_GET
def creator_social_media_refresh_status(request):
    job_id = (request.GET.get("job_id") or "").strip()
    job = _get_social_refresh_job(job_id, request.user.id)
    if not job:
        return JsonResponse({"status": "missing", "message": "Refresh job was not found."}, status=404)

    return JsonResponse(
        {
            "job_id": job_id,
            "status": job.get("status", "queued"),
            "step": job.get("step", "queued"),
            "message": job.get("message", "Preparing social analytics refresh..."),
            "error": job.get("error", ""),
        }
    )


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
            pr = PartnershipRequest.objects.filter(merchant=link.merchant, creator=request.user).order_by('-created_at').first()
            if pr:
                pr.status = REQUEST_STATUS_ACCEPTED
                pr.save(update_fields=['status', 'updated_at'])
        elif action == "decline":
            link.status = STATUS_INACTIVE
            link.save()
            pr = PartnershipRequest.objects.filter(merchant=link.merchant, creator=request.user).order_by('-created_at').first()
            if pr:
                pr.status = REQUEST_STATUS_DECLINED
                pr.save(update_fields=['status', 'updated_at'])

    return redirect("creator_affiliate_companies")
