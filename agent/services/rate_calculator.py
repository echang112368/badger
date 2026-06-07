"""Structured creator campaign rate calculator service.

The calculations in this module are deterministic so they can be tested without
calling an LLM. The OpenAI Agents SDK wrapper uses ``calculate_creator_rate`` as
its structured tool.
"""
from __future__ import annotations

from typing import Any

PLATFORMS = {"instagram", "tiktok", "youtube", "linkedin", "x", "newsletter", "blog", "podcast"}
CONTENT_FORMATS = {
    "reel", "tiktok video", "story", "static post", "carousel", "youtube integration",
    "youtube dedicated video", "short", "newsletter feature", "blog post", "podcast mention",
}
BRAND_TYPES = {"startup", "small business", "mid-market", "enterprise"}
PRIOR_DEALS = {"0", "1-3", "4-10", "10+"}
COMPLEXITY = {"low", "medium", "high"}
URGENCY = {"48 hours", "3-5 days", "1 week", "2+ weeks"}
USAGE_RIGHTS = {
    "organic creator post only", "brand organic reposting", "paid ad usage 30 days",
    "paid ad usage 90 days", "paid ad usage 6 months", "paid ad usage 1 year",
    "perpetual paid usage", "full content buyout",
}
WHITELISTING_DURATIONS = {"none", "30 days", "90 days", "6 months", "1 year", "perpetual"}

CPM_RANGES = {
    ("instagram", "story"): (10, 25),
    ("instagram", "static post"): (15, 35),
    ("instagram", "carousel"): (20, 45),
    ("instagram", "reel"): (25, 75),
    ("tiktok", "tiktok video"): (20, 60),
    ("youtube", "short"): (20, 60),
    ("youtube", "youtube integration"): (50, 150),
    ("youtube", "youtube dedicated video"): (75, 250),
    ("linkedin", "static post"): (40, 150),
    ("linkedin", "carousel"): (40, 150),
    ("linkedin", "reel"): (40, 150),
    ("linkedin", "story"): (40, 150),
    ("x", "static post"): (15, 45),
    ("newsletter", "newsletter feature"): (50, 200),
    ("blog", "blog post"): (40, 150),
    ("podcast", "podcast mention"): (50, 200),
}
NICHE_MULTIPLIERS = {
    "finance": 1.5,
    "saas/tech": 1.4,
    "tech": 1.4,
    "saas": 1.4,
    "legal": 1.5,
    "insurance": 1.5,
    "real estate": 1.35,
    "health/wellness": 1.25,
    "health": 1.25,
    "wellness": 1.25,
    "beauty/skincare": 1.15,
    "beauty": 1.15,
    "skincare": 1.15,
    "fitness": 1.15,
    "fashion": 1.1,
    "food": 1.0,
    "travel": 1.0,
    "lifestyle": 1.0,
    "entertainment/humor": 0.9,
    "entertainment": 0.9,
    "humor": 0.9,
}
PRODUCTION_MULTIPLIERS = {"low": 1.0, "medium": 1.25, "high": 1.5}
USAGE_MULTIPLIER_DELTAS = {
    "organic creator post only": 0.0,
    "brand organic reposting": 0.20,
    "paid ad usage 30 days": 0.50,
    "paid ad usage 90 days": 1.00,
    "paid ad usage 6 months": 1.75,
    "paid ad usage 1 year": 2.50,
    "perpetual paid usage": 3.00,
    "full content buyout": 2.50,
}
WHITELISTING_MULTIPLIER_DELTAS = {"none": 0.0, "30 days": 0.50, "90 days": 1.00, "6 months": 1.75, "1 year": 2.50, "perpetual": 3.00}

IMPORTANT_FIELDS = [
    "platform", "content_format", "average_views", "engagement_rate", "audience_tier1_percentage",
    "niche", "number_of_deliverables", "brand_type", "prior_paid_brand_deals", "production_complexity",
    "deadline_urgency", "usage_rights_requested",
]

FIELD_LABELS = {
    "platform": "Platform",
    "content_format": "Content format",
    "follower_count": "Follower count",
    "average_views": "Average views for this format",
    "engagement_rate": "Engagement rate",
    "average_likes": "Average likes",
    "average_comments": "Average comments",
    "average_saves": "Average saves",
    "average_shares": "Average shares",
    "audience_tier1_percentage": "Tier 1 audience percentage (US, UK, Canada, Australia)",
    "niche": "Niche/category",
    "number_of_deliverables": "Number of deliverables",
    "brand_type": "Brand type",
    "prior_paid_brand_deals": "Prior paid brand deals",
    "inbound_brand_inquiries_per_month": "Inbound brand inquiries per month",
    "production_complexity": "Production complexity",
    "deadline_urgency": "Deadline urgency",
    "usage_rights_requested": "Usage rights requested",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace("  ", " ")


def _number(payload: dict[str, Any], key: str, default: float | None = None) -> float | None:
    value = payload.get(key)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: float) -> int:
    if value <= 0:
        return 0
    return int(round(value / 25.0) * 25)


def _pct_label(delta: float) -> str:
    return f"{delta * 100:+.0f}%"


def validate_rate_input(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    missing = [FIELD_LABELS[f] for f in IMPORTANT_FIELDS if payload.get(f) in (None, "")]
    invalid: list[str] = []

    platform = _norm(payload.get("platform"))
    content_format = _norm(payload.get("content_format"))
    if payload.get("platform") not in (None, "") and platform not in PLATFORMS:
        invalid.append("Platform must be one of: " + ", ".join(sorted(PLATFORMS)) + ".")
    if payload.get("content_format") not in (None, "") and content_format not in CONTENT_FORMATS:
        invalid.append("Content format is not supported for the calculator.")
    if platform and content_format and (platform, content_format) not in CPM_RANGES:
        invalid.append("That platform/content format combination is not supported yet.")

    enums = [
        ("brand_type", BRAND_TYPES, "Brand type"),
        ("prior_paid_brand_deals", PRIOR_DEALS, "Prior paid brand deals"),
        ("production_complexity", COMPLEXITY, "Production complexity"),
        ("deadline_urgency", URGENCY, "Deadline urgency"),
        ("usage_rights_requested", USAGE_RIGHTS, "Usage rights"),
    ]
    for key, choices, label in enums:
        if payload.get(key) not in (None, "") and _norm(payload.get(key)) not in choices:
            invalid.append(f"{label} must be one of: {', '.join(sorted(choices))}.")

    whitelist = _norm(payload.get("whitelisting_duration") or "none")
    if whitelist not in WHITELISTING_DURATIONS:
        invalid.append("Whitelisting/Spark Ads duration must be one of: none, 30 days, 90 days, 6 months, 1 year, perpetual.")

    numeric_rules = [
        ("average_views", 1, None), ("engagement_rate", 0, 100), ("audience_tier1_percentage", 0, 100),
        ("number_of_deliverables", 1, 100), ("follower_count", 0, None), ("average_likes", 0, None),
        ("average_comments", 0, None), ("average_saves", 0, None), ("average_shares", 0, None),
        ("inbound_brand_inquiries_per_month", 0, None), ("exclusivity_duration_days", 0, None),
        ("campaign_duration_days", 0, None), ("average_monthly_brand_income", 0, None),
    ]
    for key, minimum, maximum in numeric_rules:
        if payload.get(key) in (None, ""):
            continue
        value = _number(payload, key)
        if value is None or value < minimum or (maximum is not None and value > maximum):
            range_text = f"at least {minimum}" if maximum is None else f"between {minimum} and {maximum}"
            invalid.append(f"{FIELD_LABELS.get(key, key)} must be {range_text}.")
    return missing, invalid


def _engagement_delta(rate: float) -> float:
    if rate < 1:
        return -0.20
    if rate < 2:
        return -0.10
    if rate <= 4:
        return 0.0
    if rate <= 8:
        return 0.25
    return 0.50


def _audience_delta(tier1_percentage: float) -> float:
    if tier1_percentage >= 80:
        return 0.25
    if tier1_percentage >= 60:
        return 0.15
    if tier1_percentage >= 40:
        return 0.0
    return -0.10


def _prior_deals_per_month(prior: str) -> float:
    return {"0": 0.5, "1-3": 1.0, "4-10": 2.0, "10+": 3.0}.get(prior, 0.5)


def _rush_delta(urgency: str) -> float:
    return {"48 hours": 0.50, "3-5 days": 0.35, "1 week": 0.20, "2+ weeks": 0.0}.get(urgency, 0.0)


def _bundle_delta(deliverables: int) -> float:
    if deliverables >= 5:
        return 0.15
    if deliverables >= 3:
        return 0.10
    if deliverables == 2:
        return 0.05
    return 0.0


def _confidence(missing: list[str], assumptions: list[str], invalid: list[str]) -> str:
    if invalid or len(missing) >= 4:
        return "low"
    if missing or len(assumptions) >= 4:
        return "medium"
    return "high"


def calculate_creator_rate(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate a transparent creator campaign rate recommendation.

    The return value intentionally mirrors the frontend contract and includes
    missing input labels instead of inventing major pricing inputs.
    """
    missing, invalid = validate_rate_input(payload)
    assumptions: list[str] = []
    risk_flags: list[str] = []

    if invalid:
        return _empty_response(missing, invalid, assumptions, risk_flags)
    if missing:
        return _empty_response(missing, [], assumptions, risk_flags)

    platform = _norm(payload.get("platform"))
    content_format = _norm(payload.get("content_format"))
    follower_count = _number(payload, "follower_count", 0) or 0
    average_views = _number(payload, "average_views", 0) or 0
    engagement_rate = _number(payload, "engagement_rate", 0) or 0
    tier1 = _number(payload, "audience_tier1_percentage", 40) or 40
    deliverables = int(_number(payload, "number_of_deliverables", 1) or 1)
    niche = _norm(payload.get("niche"))
    brand_type = _norm(payload.get("brand_type"))
    prior_deals = _norm(payload.get("prior_paid_brand_deals"))
    inbound = _number(payload, "inbound_brand_inquiries_per_month", None)
    production = _norm(payload.get("production_complexity"))
    urgency = _norm(payload.get("deadline_urgency"))
    usage = _norm(payload.get("usage_rights_requested"))
    whitelisting_requested = bool(payload.get("whitelisting_requested"))
    whitelist_duration = _norm(payload.get("whitelisting_duration") or ("30 days" if whitelisting_requested else "none"))
    exclusivity_requested = bool(payload.get("exclusivity_requested"))
    exclusivity_days = int(_number(payload, "exclusivity_duration_days", 0) or 0)
    exclusivity_scope = _norm(payload.get("exclusivity_scope") or "direct competitors")
    restricts_organic = bool(payload.get("exclusivity_restricts_organic_content"))
    raw_footage = bool(payload.get("raw_footage_requested"))
    cross_platform = bool(payload.get("cross_platform_usage_requested"))
    paid_ad_usage = bool(payload.get("paid_ad_usage_requested"))
    perpetual_rights = bool(payload.get("perpetual_rights_requested"))
    monthly_income = _number(payload, "average_monthly_brand_income", None)

    if follower_count == 0:
        assumptions.append("Follower count was not provided; the estimate relies on average views as the primary pricing signal.")
    for minor in ["average_likes", "average_comments", "average_saves", "average_shares", "campaign_duration_days"]:
        if payload.get(minor) in (None, ""):
            assumptions.append(f"{FIELD_LABELS.get(minor, minor)} was not provided; it was not used as a core pricing driver.")
    if inbound is None:
        assumptions.append("Inbound brand inquiries were not provided; demand was estimated from prior paid deal history.")
        inbound = 0
    if whitelisting_requested and whitelist_duration == "none":
        whitelist_duration = "30 days"
        assumptions.append("Whitelisting/Spark Ads was requested without a duration, so 30 days was assumed conservatively.")
    if exclusivity_requested and exclusivity_days <= 0:
        exclusivity_days = 30
        assumptions.append("Exclusivity was requested without a duration, so 30 days was assumed conservatively.")
    if paid_ad_usage and usage == "organic creator post only":
        usage = "paid ad usage 30 days"
        assumptions.append("Paid ad usage was marked requested while usage rights said organic only; 30 days of paid usage was assumed.")
    if perpetual_rights:
        usage = "perpetual paid usage"

    cpm_low, cpm_high = CPM_RANGES[(platform, content_format)]
    cpm_midpoint = (cpm_low + cpm_high) / 2
    base_rate = average_views / 1000 * cpm_midpoint

    engagement_delta = _engagement_delta(engagement_rate)
    engagement_adjustment = base_rate * engagement_delta
    audience_delta = _audience_delta(tier1)
    audience_quality_adjustment = base_rate * audience_delta
    pre_niche = base_rate + engagement_adjustment + audience_quality_adjustment
    niche_multiplier = NICHE_MULTIPLIERS.get(niche, 1.0)
    if niche not in NICHE_MULTIPLIERS:
        assumptions.append("Niche was not in the calculator's premium table, so a 1.0x niche multiplier was used.")
    niche_premium = pre_niche * (niche_multiplier - 1)
    single_rate_before_production = max(0, pre_niche + niche_premium)

    content_before_production = single_rate_before_production * deliverables
    production_fee = content_before_production * (PRODUCTION_MULTIPLIERS[production] - 1)
    content_subtotal = content_before_production + production_fee

    bundle_discount_rate = _bundle_delta(deliverables)
    bundle_discount = content_subtotal * bundle_discount_rate
    discounted_content_subtotal = content_subtotal - bundle_discount

    usage_delta = USAGE_MULTIPLIER_DELTAS[usage]
    if raw_footage:
        usage_delta += {"low": 0.25, "medium": 0.50, "high": 0.75}[production]
    if cross_platform:
        usage_delta += 0.25
    usage_rights_fee = content_subtotal * usage_delta

    whitelisting_fee = content_subtotal * WHITELISTING_MULTIPLIER_DELTAS[whitelist_duration]

    estimated_deals = max(_prior_deals_per_month(prior_deals), min(inbound * 0.4, 5.0))
    estimated_monthly_deal_value = monthly_income if monthly_income is not None else single_rate_before_production * estimated_deals
    exclusivity_fee = 0.0
    if exclusivity_requested:
        exclusivity_fee = (estimated_monthly_deal_value / 30) * exclusivity_days
        if exclusivity_scope and exclusivity_scope != "direct competitors":
            exclusivity_fee *= 1.5
        if restricts_organic:
            risk_flags.append("Exclusivity restricts organic content. This can materially limit your normal posting and should be reviewed before accepting.")

    rush_fee = discounted_content_subtotal * _rush_delta(urgency)

    if usage == "perpetual paid usage" or whitelist_duration == "perpetual":
        risk_flags.append("Perpetual paid usage is high risk. Strongly consider avoiding it or pricing it at 3x+ with professional contract review.")
    if usage == "full content buyout":
        risk_flags.append("Full content buyout transfers broad value to the brand. Consider manager or lawyer review before signing.")
    if exclusivity_days >= 90:
        risk_flags.append("Long exclusivity periods can block future deals and should be scoped narrowly.")
    if brand_type == "enterprise" and usage_delta > 0:
        risk_flags.append("Enterprise paid usage can create significant advertising value; make sure duration, platforms, and territories are written clearly.")

    target = discounted_content_subtotal + usage_rights_fee + whitelisting_fee + exclusivity_fee + rush_fee
    demand_score = 0
    if prior_deals == "10+":
        demand_score += 1
    if inbound >= 4:
        demand_score += 1
    if brand_type == "enterprise":
        demand_score += 1
    ceiling_multiplier = min(1.4, 1.2 + 0.05 * demand_score)
    floor_multiplier = 0.82 if demand_score >= 1 else 0.80

    floor = _money(target * floor_multiplier)
    target_rounded = _money(target)
    ceiling = _money(target * ceiling_multiplier)

    base_label = f"{platform.title()} {content_format.title()} uses a ${cpm_midpoint:.2f} midpoint CPM from the ${cpm_low}-${cpm_high} CPM range."
    explanation = (
        f"Your recommended target rate is ${target_rounded:,.0f}. {base_label} "
        f"With {average_views:,.0f} average views, the base rate is ${_money(base_rate):,.0f}. "
        f"Your {engagement_rate:.1f}% engagement rate applies a {_pct_label(engagement_delta)} performance adjustment, "
        f"and your {tier1:.0f}% Tier 1 audience applies a {_pct_label(audience_delta)} audience quality adjustment. "
        f"The {niche or 'selected'} niche uses a {niche_multiplier:.2f}x multiplier, production complexity is priced separately, "
        f"and usage, whitelisting, exclusivity, and rush timing are separate line items. Creator rates are not standardized; "
        f"this is a data-backed estimate, not a guaranteed market price. This is not legal advice."
    )
    if risk_flags:
        explanation += " For large contracts, exclusivity, perpetual usage, full buyouts, or ambassador deals, consider consulting a lawyer or manager."

    anchor = (
        f"My rate reflects the performance of my average {content_format.title()} content, "
        f"the requested usage/license terms, and any exclusivity or timeline constraints that affect my ability to take other partnerships."
    )
    package = _package_suggestion(deliverables, usage, whitelisting_requested, exclusivity_requested, target_rounded)

    line_items = {
        "base_rate": _money(base_rate * deliverables),
        "engagement_adjustment": _money(engagement_adjustment * deliverables),
        "audience_quality_adjustment": _money(audience_quality_adjustment * deliverables),
        "niche_premium": _money(niche_premium * deliverables),
        "production_complexity_fee": _money(production_fee),
        "usage_rights_fee": _money(usage_rights_fee),
        "whitelisting_fee": _money(whitelisting_fee),
        "exclusivity_fee": _money(exclusivity_fee),
        "rush_fee": _money(rush_fee),
        "bundle_discount": -_money(bundle_discount),
        "final_target_rate": target_rounded,
    }

    return {
        "rate_recommendation": {"floor": floor, "target": target_rounded, "ceiling": ceiling, "currency": "USD"},
        "line_item_breakdown": line_items,
        "calculation_notes": {
            "cpm_range": {"low": cpm_low, "high": cpm_high, "selected_midpoint": cpm_midpoint},
            "engagement_multiplier_delta": engagement_delta,
            "audience_quality_multiplier_delta": audience_delta,
            "niche_multiplier": niche_multiplier,
            "production_multiplier": PRODUCTION_MULTIPLIERS[production],
            "usage_multiplier_delta": usage_delta,
            "whitelisting_multiplier_delta": WHITELISTING_MULTIPLIER_DELTAS[whitelist_duration],
            "bundle_discount_rate": bundle_discount_rate,
            "rush_fee_rate": _rush_delta(urgency),
            "estimated_deals_per_month": estimated_deals,
        },
        "assumptions": assumptions,
        "missing_inputs": [],
        "risk_flags": risk_flags,
        "creator_explanation": explanation,
        "brand_negotiation_anchor": anchor,
        "pushback_responses": _pushback_responses(),
        "package_suggestion": package,
        "confidence_level": _confidence([], assumptions, []),
    }


def _empty_response(missing: list[str], invalid: list[str], assumptions: list[str], risk_flags: list[str]) -> dict[str, Any]:
    followup = "Please add the missing required inputs so I can calculate a transparent floor, target, and ceiling rate."
    if invalid:
        followup = "Please correct the invalid inputs before calculating a rate."
    return {
        "rate_recommendation": {"floor": 0, "target": 0, "ceiling": 0, "currency": "USD"},
        "line_item_breakdown": {
            "base_rate": 0,
            "engagement_adjustment": 0,
            "audience_quality_adjustment": 0,
            "niche_premium": 0,
            "production_complexity_fee": 0,
            "usage_rights_fee": 0,
            "whitelisting_fee": 0,
            "exclusivity_fee": 0,
            "rush_fee": 0,
            "bundle_discount": 0,
            "final_target_rate": 0,
        },
        "assumptions": assumptions,
        "missing_inputs": missing,
        "invalid_inputs": invalid,
        "risk_flags": risk_flags,
        "creator_explanation": followup + " Creator rates are not standardized; this is a data-backed estimate, not a guaranteed market price.",
        "brand_negotiation_anchor": "",
        "pushback_responses": _pushback_responses(),
        "package_suggestion": "",
        "confidence_level": _confidence(missing, assumptions, invalid),
    }


def _pushback_responses() -> list[str]:
    return [
        "If the brand says the rate is too high: I can adjust scope to fit budget, such as reducing deliverables, removing paid usage, or shortening exclusivity.",
        "If the brand asks for free usage rights: Organic posting is included in the creator post fee, but paid or brand usage is a separate license because it creates additional value for the brand.",
        "If the brand asks for perpetual rights: I do not include perpetual usage by default because it is broad and hard to value; I can quote a higher buyout-style license or limit usage to a defined term.",
        "If the brand wants exclusivity included for free: Exclusivity prevents me from accepting other partnerships, so it needs to be priced as a separate opportunity-cost line item.",
        "If the brand compares me to cheaper creators: My pricing is based on my average content performance, audience quality, production scope, and the rights requested for this specific campaign.",
    ]


def _package_suggestion(deliverables: int, usage: str, whitelisting: bool, exclusivity: bool, target: int) -> str:
    base = f"Package recommendation: quote ${target:,.0f} as the target package."
    parts = []
    if deliverables > 1:
        parts.append(f"Keep the {deliverables}-deliverable bundle discount tied only to content production, not licensing.")
    if usage != "organic creator post only":
        parts.append("Show usage rights as a separate license with a clear duration and platform scope.")
    if whitelisting:
        parts.append("List Whitelisting/Spark Ads as its own add-on so ad access is not bundled into organic content.")
    if exclusivity:
        parts.append("Separate exclusivity by category, competitor set, geography, and number of days.")
    if not parts:
        parts.append("Offer one organic post package and a higher optional package that includes paid usage if the brand needs ads.")
    return base + " " + " ".join(parts)
