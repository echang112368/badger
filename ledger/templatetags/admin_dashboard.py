from datetime import timedelta
from decimal import Decimal

from django import template
from django.db.models import Count, Sum
from django.utils import timezone

from accounts.models import CustomUser
from collect.models import AffiliateClick, ReferralConversion
from creators.models import CreatorMeta
from ledger.models import LedgerEntry, MerchantInvoice
from links.models import MerchantCreatorLink, STATUS_ACTIVE
from merchants.models import MerchantMeta

register = template.Library()


def _quantize_amount(value: Decimal | None) -> Decimal:
    """Return a two-decimal-place Decimal for display purposes."""

    if value is None:
        value = Decimal("0")
    if not isinstance(value, Decimal):
        try:
            value = Decimal(value)
        except Exception:
            value = Decimal("0")
    return value.quantize(Decimal("0.01"))


@register.inclusion_tag("admin/dashboard.html", takes_context=False, name="admin_dashboard")
def render_admin_dashboard():
    """Render an operational snapshot for the Django admin landing page."""

    now = timezone.now()
    today = timezone.localdate()
    week_start = now - timedelta(days=7)
    prev_week_start = week_start - timedelta(days=7)
    month_start = now - timedelta(days=30)

    conversions_today = ReferralConversion.objects.filter(created_at__date=today)
    conversions_week = ReferralConversion.objects.filter(created_at__gte=week_start)
    conversions_prev_week = ReferralConversion.objects.filter(
        created_at__gte=prev_week_start, created_at__lt=week_start
    )
    conversions_month = ReferralConversion.objects.filter(created_at__gte=month_start)

    revenue_today = _quantize_amount(
        conversions_today.aggregate(total=Sum("order_amount"))["total"]
    )
    commission_today = _quantize_amount(
        conversions_today.aggregate(total=Sum("commission_amount"))["total"]
    )

    payout_entries = LedgerEntry.objects.filter(
        paid=False,
        entry_type__in=[
            LedgerEntry.EntryType.PAYOUT,
            LedgerEntry.EntryType.AFFILIATE_PAYOUT,
            LedgerEntry.EntryType.BADGER_PAYOUT,
        ],
    )

    monthly_fee_total = _quantize_amount(
        MerchantMeta.objects.aggregate(total=Sum("monthly_fee"))["total"]
    )
    monthly_fee_merchants = MerchantMeta.objects.filter(monthly_fee__gt=0).count()

    default_creator = CustomUser.get_default_badger_creator()
    default_creator_merchants = []
    default_creator_total = Decimal("0")

    if default_creator:
        default_creator_entries = LedgerEntry.objects.filter(creator=default_creator)
        default_creator_total = _quantize_amount(
            default_creator_entries.aggregate(total=Sum("amount"))["total"]
        )
        default_creator_merchants = list(
            default_creator_entries.values(
                "merchant_id", "merchant__email", "merchant__username"
            )
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )

    summary = {
        "revenue_today": revenue_today,
        "commission_today": commission_today,
        "conversions_today": conversions_today.count(),
        "pending_payouts": _quantize_amount(
            payout_entries.aggregate(total=Sum("amount"))["total"]
        ),
        "unpaid_commissions": _quantize_amount(
            LedgerEntry.objects.filter(
                entry_type=LedgerEntry.EntryType.COMMISSION, paid=False
            ).aggregate(total=Sum("amount"))["total"]
        ),
        "open_invoices": MerchantInvoice.objects.exclude(status__iexact="paid").count(),
        "active_merchants": MerchantMeta.objects.count(),
        "active_creators": CreatorMeta.objects.count(),
        "live_links": MerchantCreatorLink.objects.filter(status=STATUS_ACTIVE).count(),
        "monthly_fee_total": monthly_fee_total,
        "monthly_fee_merchants": monthly_fee_merchants,
    }

    click_volume_week = AffiliateClick.objects.filter(created_at__gte=week_start).count()
    conversion_volume_week = conversions_week.count()
    conversion_rate = None
    if click_volume_week:
        conversion_rate = round((conversion_volume_week / click_volume_week) * 100, 2)

    top_affiliates = list(
        conversions_month.filter(creator__isnull=False)
        .values("creator_id", "creator__email", "creator__username")
        .annotate(conversions=Count("id"), revenue=Sum("order_amount"))
        .order_by("-revenue")[:5]
    )

    top_merchants = list(
        conversions_month.filter(merchant__isnull=False)
        .values("merchant_id", "merchant__email", "merchant__username")
        .annotate(conversions=Count("id"), revenue=Sum("order_amount"))
        .order_by("-revenue")[:5]
    )

    canceled_merchants = list(
        MerchantMeta.objects.filter(shopify_uninstalled_at__isnull=False)
        .values(
            "company_name",
            "shopify_store_domain",
            "shopify_uninstalled_at",
            "user__email",
            "user__username",
        )
        .order_by("-shopify_uninstalled_at")[:10]
    )

    alerts: list[dict[str, str]] = []
    previous_count = conversions_prev_week.count()
    if previous_count and conversion_volume_week < previous_count * 0.5:
        alerts.append(
            {
                "title": "Conversion drop",
                "detail": "This week is trending more than 50% below the prior week. Investigate tracking and offer health.",
            }
        )

    if summary["pending_payouts"] > Decimal("0"):
        alerts.append(
            {
                "title": "Payouts awaiting approval",
                "detail": "Review and approve pending payout entries to keep partners current.",
            }
        )

    if conversion_rate is not None and conversion_rate < 1:
        alerts.append(
            {
                "title": "Low click→conversion rate",
                "detail": "Recent traffic is converting under 1%. Check creative quality or landing page issues.",
            }
        )

    return {
        "summary": summary,
        "conversion_rate": conversion_rate,
        "weekly_clicks": click_volume_week,
        "weekly_conversions": conversion_volume_week,
        "top_affiliates": top_affiliates,
        "top_merchants": top_merchants,
        "alerts": alerts,
        "default_creator": default_creator,
        "default_creator_total": default_creator_total,
        "default_creator_merchants": default_creator_merchants,
        "canceled_merchants": canceled_merchants,
    }
