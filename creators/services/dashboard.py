from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db.models import Count
from django.utils import timezone

from collect.models import AffiliateClick, ReferralConversion, ReferralVisit
from collect.utils import compute_commission_schedule
from creators.models import CreatorMeta, SocialAnalyticsSnapshot
from instagram_connect.models import InstagramConnection
from ledger.models import LedgerEntry
from links.models import (
    MerchantCreatorLink,
    PartnershipRequest,
    REQUEST_STATUS_ACCEPTED,
    REQUEST_STATUS_PENDING,
    STATUS_ACTIVE,
    STATUS_REQUESTED,
)


@dataclass(frozen=True)
class EarningsSummary:
    pending: Decimal
    total: Decimal
    last_30_days: Decimal
    labels: list[str]
    totals: list[float]


def _money(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        value = Decimal("0")
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_earnings_summary(user) -> EarningsSummary:
    """Calculate the creator earnings data used by dashboard and earnings pages."""
    conversions = ReferralConversion.objects.filter(creator=user).select_related("merchant")
    now = timezone.now()
    last_30_days_start = now - timedelta(days=30)
    pending_earnings = Decimal("0")
    available_earnings = Decimal("0")
    last_30_days_earnings = Decimal("0")
    monthly_totals: dict[date, Decimal] = {}

    for conversion in conversions:
        breakdown = compute_commission_schedule(conversion, conversion.merchant)
        for commission, return_days in breakdown:
            if commission <= 0:
                continue
            release_date = conversion.created_at + timedelta(days=return_days)
            if now >= release_date:
                available_earnings += commission
                month_bucket = conversion.created_at.date().replace(day=1)
                monthly_totals[month_bucket] = (
                    monthly_totals.get(month_bucket, Decimal("0")) + commission
                )
                if conversion.created_at >= last_30_days_start:
                    last_30_days_earnings += commission
            else:
                pending_earnings += commission

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

    return EarningsSummary(
        pending=_money(pending_earnings),
        total=_money(available_earnings),
        last_30_days=_money(last_30_days_earnings),
        labels=[month_start.strftime("%b %Y") for month_start in months],
        totals=[float(_money(monthly_totals.get(month_start, Decimal("0")))) for month_start in months],
    )


def _profile_missing_items(creator_meta: CreatorMeta) -> list[dict[str, str]]:
    platform, follower_range, _ = creator_meta.primary_platform_data()
    languages = [
        part.strip()
        for part in (creator_meta.content_languages or "").split(",")
        if part.strip()
    ]
    skills = [skill for skill in (creator_meta.content_skills or []) if skill]
    checks = [
        ("primary social platform", bool(platform)),
        ("follower range", bool(follower_range)),
        ("country", bool((creator_meta.country or "").strip())),
        ("content languages", bool(languages)),
        ("content skills", bool(skills)),
    ]
    return [
        {"label": label.title(), "url_name": "creator_profile"}
        for label, complete in checks
        if not complete
    ]


def _build_setup_steps(
    creator_meta: CreatorMeta,
    missing_items: list[dict[str, str]],
    social: dict[str, Any],
    performance: dict[str, Any],
) -> list[dict[str, Any]]:
    profile_complete = not missing_items
    social_connected = bool(social["connected"])
    analyzer_complete = social_connected and social.get("ai_score") is not None
    marketplace_visible = bool(creator_meta.marketplace_enabled)
    first_link_created = performance["active_links"] > 0

    raw_steps = [
        {
            "title": "Profile basics",
            "description": "Tell brands what you create, where you are based, and who you reach.",
            "url_name": "creator_profile",
            "cta": "Finish profile",
            "complete": profile_complete,
            "icon": "bi-person-lines-fill",
            "helper": (
                "Ready for merchant review"
                if profile_complete
                else f"{len(missing_items)} detail{'s' if len(missing_items) != 1 else ''} left"
            ),
        },
        {
            "title": "Connect socials",
            "description": "Link Instagram so Badger can verify audience signals and content fit.",
            "url_name": "creator_social_media",
            "cta": "Connect Instagram",
            "complete": social_connected,
            "icon": "bi-instagram",
            "helper": (
                f"@{social['username']} connected"
                if social_connected and social.get("username")
                else "Not connected"
            ),
        },
        {
            "title": "Get AI readiness tips",
            "description": "Review analyzer feedback before you pitch or respond to brand requests.",
            "url_name": "creator_social_media",
            "cta": "Run analyzer",
            "complete": analyzer_complete,
            "icon": "bi-stars",
            "helper": (
                f"Score {social['ai_score']}/100"
                if analyzer_complete
                else "Analyzer waiting on social data"
            ),
        },
        {
            "title": "Publish to marketplace",
            "description": "Turn on discoverability so brands can invite you into partnerships.",
            "url_name": "creator_marketplace",
            "cta": "Publish profile",
            "complete": marketplace_visible,
            "icon": "bi-shop-window",
            "helper": (
                "Visible to merchants"
                if marketplace_visible
                else "Hidden from marketplace"
            ),
        },
        {
            "title": "Create first link",
            "description": "Choose an offer and generate a trackable link to start earning.",
            "url_name": "creator_marketplace",
            "cta": "Find an offer",
            "complete": first_link_created,
            "icon": "bi-link-45deg",
            "helper": (
                f"{performance['active_links']} active link{'s' if performance['active_links'] != 1 else ''}"
                if first_link_created
                else "No active links yet"
            ),
        },
    ]

    for index, step in enumerate(raw_steps, start=1):
        step["number"] = index
        step["state"] = "complete" if step["complete"] else "upcoming"

    current_step = next((step for step in raw_steps if not step["complete"]), None)
    if current_step:
        current_step["state"] = "current"

    return raw_steps


def _social_summary(user) -> dict[str, Any]:
    connection = getattr(user, "instagram_connection", None)
    if connection is None:
        connection = InstagramConnection.objects.filter(user=user).first()

    summary: dict[str, Any] = {
        "connected": bool(connection),
        "username": "",
        "followers_count": 0,
        "media_count": 0,
        "last_synced_at": None,
        "ai_score": None,
        "ai_verdict": "",
        "ai_summary": "",
        "top_actions": [],
    }
    if not connection:
        return summary

    summary.update(
        {
            "username": connection.instagram_username,
            "followers_count": connection.followers_count,
            "media_count": connection.media_count,
            "last_synced_at": connection.last_synced_at or connection.connected_at,
        }
    )

    snapshot = SocialAnalyticsSnapshot.objects.filter(
        user=user,
        platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
    ).first()
    payload = snapshot.payload if snapshot else {}
    ai_feedback = ((payload or {}).get("_ai_cache") or {}).get("feedback") or {}
    if ai_feedback:
        summary.update(
            {
                "ai_score": ai_feedback.get("overall_score"),
                "ai_verdict": ai_feedback.get("verdict") or "",
                "ai_summary": ai_feedback.get("summary") or "",
                "top_actions": ai_feedback.get("top_priority_actions") or [],
            }
        )
    return summary


def _partnership_summary(user) -> dict[str, Any]:
    request_counts = {
        row["status"]: row["count"]
        for row in PartnershipRequest.objects.filter(creator=user)
        .values("status")
        .annotate(count=Count("id"))
    }
    pending_requests = list(
        PartnershipRequest.objects.filter(
            creator=user,
            status=REQUEST_STATUS_PENDING,
        )
        .select_related("merchant", "merchant__merchantmeta")
        .order_by("-created_at")[:3]
    )
    return {
        "pending_count": request_counts.get(REQUEST_STATUS_PENDING, 0),
        "accepted_count": request_counts.get(REQUEST_STATUS_ACCEPTED, 0),
        "active_count": MerchantCreatorLink.objects.filter(
            creator=user,
            status=STATUS_ACTIVE,
        ).count(),
        "requested_count": MerchantCreatorLink.objects.filter(
            creator=user,
            status=STATUS_REQUESTED,
        ).count(),
        "pending_requests": pending_requests,
    }


def _performance_summary(user, creator_meta: CreatorMeta) -> dict[str, Any]:
    referral_clicks = ReferralVisit.objects.filter(creator=user).count()
    legacy_clicks = AffiliateClick.objects.filter(uuid=creator_meta.uuid).count()
    clicks = referral_clicks or legacy_clicks
    conversions = ReferralConversion.objects.filter(creator=user).count()
    active_links = MerchantCreatorLink.objects.filter(
        creator=user,
        status=STATUS_ACTIVE,
    ).count()
    conversion_rate = Decimal("0")
    if clicks:
        conversion_rate = (Decimal(conversions) / Decimal(clicks)) * Decimal("100")
    return {
        "clicks": clicks,
        "conversions": conversions,
        "active_links": active_links,
        "conversion_rate": _money(conversion_rate),
    }


def _build_checklist(
    creator_meta: CreatorMeta,
    missing_items: list[dict[str, str]],
    social: dict[str, Any],
    partnerships: dict[str, Any],
    performance: dict[str, Any],
) -> list[dict[str, Any]]:
    checklist = []
    if missing_items:
        remaining_count = len(missing_items) - 1
        detail_copy = missing_items[0]["label"].lower()
        if remaining_count:
            detail_copy = f"{detail_copy} and {remaining_count} more profile details"
        checklist.append(
            {
                "title": "Complete your creator profile",
                "description": f"Add {detail_copy} so merchants can evaluate you faster.",
                "url_name": "creator_profile",
                "cta": "Improve profile",
                "complete": False,
                "priority": 10,
            }
        )
    if not social["connected"]:
        checklist.append(
            {
                "title": "Connect Instagram",
                "description": "Unlock social insights, profile health signals, and AI recommendations for brand readiness.",
                "url_name": "creator_social_media",
                "cta": "Connect social",
                "complete": False,
                "priority": 20,
            }
        )
    elif social.get("ai_score") is None:
        checklist.append(
            {
                "title": "Run your social profile analyzer",
                "description": "Refresh the Social Media page to generate AI feedback from your connected Instagram data.",
                "url_name": "creator_social_media",
                "cta": "View analyzer",
                "complete": False,
                "priority": 30,
            }
        )
    if not creator_meta.marketplace_enabled:
        checklist.append(
            {
                "title": "Enable marketplace visibility",
                "description": "Make your profile discoverable to merchants looking for creator partners.",
                "url_name": "creator_marketplace",
                "cta": "Open marketplace",
                "complete": False,
                "priority": 40,
            }
        )
    if partnerships["pending_count"]:
        checklist.append(
            {
                "title": "Review pending partnership requests",
                "description": f"You have {partnerships['pending_count']} request(s) waiting for a response.",
                "url_name": "creator_requests",
                "cta": "Review requests",
                "complete": False,
                "priority": 5,
            }
        )
    if performance["active_links"] == 0:
        checklist.append(
            {
                "title": "Create your first affiliate link",
                "description": "Browse marketplace opportunities or accept a partnership to start sharing products.",
                "url_name": "creator_marketplace",
                "cta": "Find merchants",
                "complete": False,
                "priority": 50,
            }
        )

    checklist.sort(key=lambda item: item["priority"])
    return checklist[:5]


def _recent_activity(user) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for req in PartnershipRequest.objects.filter(creator=user).select_related("merchant").order_by("-created_at")[:3]:
        activities.append(
            {
                "icon": "bi-envelope-paper",
                "title": f"Partnership request from {req.merchant.username}",
                "description": req.get_status_display(),
                "timestamp": req.created_at,
                "url_name": "creator_requests",
            }
        )
    for conversion in ReferralConversion.objects.filter(creator=user).select_related("merchant").order_by("-created_at")[:3]:
        merchant_name = conversion.merchant.username if conversion.merchant else "a merchant"
        activities.append(
            {
                "icon": "bi-bag-check",
                "title": f"New conversion from {merchant_name}",
                "description": f"Order amount ${conversion.order_amount}",
                "timestamp": conversion.created_at,
                "url_name": "creator_earnings",
            }
        )
    for entry in LedgerEntry.objects.filter(creator=user).order_by("-timestamp")[:3]:
        activities.append(
            {
                "icon": "bi-cash-coin",
                "title": entry.get_entry_type_display(),
                "description": f"${entry.amount} · {'Paid' if entry.paid else 'Unpaid'}",
                "timestamp": entry.timestamp,
                "url_name": "creator_earnings",
            }
        )
    activities.sort(key=lambda activity: activity["timestamp"], reverse=True)
    return activities[:5]


def build_creator_dashboard_context(user) -> dict[str, Any]:
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=user)
    earnings = build_earnings_summary(user)
    missing_items = _profile_missing_items(creator_meta)
    social = _social_summary(user)
    partnerships = _partnership_summary(user)
    performance = _performance_summary(user, creator_meta)
    checklist = _build_checklist(
        creator_meta,
        missing_items,
        social,
        partnerships,
        performance,
    )
    setup_steps = _build_setup_steps(creator_meta, missing_items, social, performance)
    setup_complete_count = sum(1 for step in setup_steps if step["complete"])
    current_setup_step = next((step for step in setup_steps if not step["complete"]), None)
    completeness_pct = round((setup_complete_count / len(setup_steps)) * 100) if setup_steps else 100
    next_action = checklist[0] if checklist else {
        "title": "Keep growing your partnerships",
        "description": "Your creator setup looks ready. Monitor requests, links, and social insights to optimize earnings.",
        "url_name": "creator_marketplace",
        "cta": "Browse marketplace",
    }
    return {
        "creator_meta": creator_meta,
        "profile": {
            "completeness_pct": completeness_pct,
            "missing_items": missing_items,
            "marketplace_enabled": creator_meta.marketplace_enabled,
            "skills_count": len([skill for skill in (creator_meta.content_skills or []) if skill]),
            "social_profiles_count": len(creator_meta.social_media_profiles or []),
        },
        "setup": {
            "steps": setup_steps,
            "complete_count": setup_complete_count,
            "total_count": len(setup_steps),
            "current_step": current_setup_step,
            "is_complete": current_setup_step is None,
        },
        "social": social,
        "partnerships": partnerships,
        "performance": performance,
        "earnings": earnings,
        "checklist": checklist,
        "next_action": next_action,
        "recent_activity": _recent_activity(user),
    }
