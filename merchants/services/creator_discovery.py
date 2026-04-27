from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from creators.models import CreatorMeta, SocialAnalyticsSnapshot
from instagram_connect.models import InstagramConnection
from merchants.models import CompanyCreatorPreferences

DISCOVERY_PLATFORM_OPTIONS = ["instagram", "tiktok", "youtube", "linkedin"]
MATCH_STRONG_THRESHOLD = 80
MATCH_POSSIBLE_THRESHOLD = 60


@dataclass
class DiscoveryFilters:
    query: str = ""
    platform: str = ""
    niche: str = ""
    follower_min: int = 0
    follower_max: int | None = None
    min_engagement_rate: float | None = None
    audience_gender: str = ""
    audience_age: str = ""
    audience_location: str = ""
    min_match_score: int | None = None
    view: str = "grid"
    use_saved_preferences: bool = True



def build_discovery_filters(params) -> DiscoveryFilters:
    def to_int(raw: str | None, default: int | None = None) -> int | None:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default

    def to_float(raw: str | None) -> float | None:
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return None

    platform = (params.get("platform") or "").strip().lower()
    if platform not in DISCOVERY_PLATFORM_OPTIONS:
        platform = ""

    view = (params.get("view") or "grid").strip().lower()
    if view not in {"grid", "list"}:
        view = "grid"

    follower_min = max(to_int(params.get("follower_min"), 0) or 0, 0)
    follower_max = to_int(params.get("follower_max"), None)
    if follower_max is not None and follower_max < follower_min:
        follower_max = follower_min

    min_match_score = to_int(params.get("min_match_score"), None)
    if min_match_score is not None:
        min_match_score = max(0, min(100, min_match_score))

    use_saved_preferences_raw = (params.get("use_preferences") or "1").strip().lower()
    use_saved_preferences = use_saved_preferences_raw not in {"0", "false", "off", "no"}

    return DiscoveryFilters(
        query=(params.get("q") or "").strip(),
        platform=platform,
        niche=(params.get("niche") or "").strip(),
        follower_min=follower_min,
        follower_max=follower_max,
        min_engagement_rate=to_float(params.get("min_engagement_rate")),
        audience_gender=(params.get("audience_gender") or "").strip().lower(),
        audience_age=(params.get("audience_age") or "").strip().lower(),
        audience_location=(params.get("audience_location") or "").strip(),
        min_match_score=min_match_score,
        view=view,
        use_saved_preferences=use_saved_preferences,
    )


def _percentage(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value <= 1:
        value *= 100
    return round(value, 2)


def _label_value(rows: list[dict[str, Any]], startswith: str) -> float:
    for row in rows:
        label = str(row.get("label") or "").lower()
        if label.startswith(startswith):
            try:
                return float(row.get("value") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _top_demographic_label(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    top = max(rows, key=lambda row: float(row.get("value") or 0))
    return str(top.get("label") or "")


def _text_contains_any(content: str, terms: list[str]) -> bool:
    lower_content = (content or "").lower()
    return any(term in lower_content for term in terms if term)


def _preferences_score_adjustment(
    card: dict[str, Any],
    preferences: CompanyCreatorPreferences | None,
) -> int:
    if not preferences:
        return 0

    adjustment = 0
    engagement_rate = card.get("engagement_rate") or 0
    average_reach = card.get("average_reach") or 0
    profile_views = card.get("profile_views") or 0
    website_clicks = card.get("website_clicks") or 0
    comment_rate = card.get("average_comment_rate") or 0
    save_rate = card.get("average_save_rate") or 0
    share_rate = card.get("average_share_rate") or 0

    if preferences.campaign_goal == CompanyCreatorPreferences.CampaignGoal.BRAND_AWARENESS:
        if average_reach >= 15000:
            adjustment += 8
        if share_rate and share_rate >= 1.0:
            adjustment += 3
    elif preferences.campaign_goal == CompanyCreatorPreferences.CampaignGoal.CONVERSIONS_SALES:
        if profile_views:
            adjustment += min(8, int(profile_views / 60))
        if website_clicks:
            adjustment += min(10, int(website_clicks / 25))
        if save_rate and save_rate >= 1.2:
            adjustment += 3
    elif preferences.campaign_goal == CompanyCreatorPreferences.CampaignGoal.UGC_CONTENT_CREATION:
        if card.get("niche_text"):
            adjustment += 3
        if engagement_rate and engagement_rate >= 2.5:
            adjustment += 4
    elif preferences.campaign_goal == CompanyCreatorPreferences.CampaignGoal.COMMUNITY_GROWTH:
        if comment_rate and comment_rate >= 0.6:
            adjustment += 7
        if engagement_rate and engagement_rate >= 3.5:
            adjustment += 4

    if preferences.performance_priority == CompanyCreatorPreferences.PerformancePriority.REACH:
        if average_reach >= 20000:
            adjustment += 7
    elif preferences.performance_priority == CompanyCreatorPreferences.PerformancePriority.ENGAGEMENT:
        if engagement_rate >= 4.0:
            adjustment += 7
    elif preferences.performance_priority == CompanyCreatorPreferences.PerformancePriority.CONVERSIONS:
        adjustment += min(8, int(website_clicks / 30))
        adjustment += min(5, int(profile_views / 120))
    elif preferences.performance_priority == CompanyCreatorPreferences.PerformancePriority.CONTENT_QUALITY:
        if save_rate >= 1.5:
            adjustment += 5
        if share_rate >= 1.0:
            adjustment += 4

    style_keywords = {
        "educational": ["educat", "tutorial", "how-to"],
        "lifestyle": ["lifestyle", "daily"],
        "comedic": ["comedy", "funny", "humor"],
        "aesthetic": ["aesthetic", "visual"],
        "review_testimonial": ["review", "testimonial", "comparison"],
        "storytelling": ["story", "storytelling", "journey"],
        "technical_expert": ["technical", "expert", "deep dive"],
    }
    niche_text = card.get("niche_text", "")
    matched_styles = 0
    for style in preferences.preferred_creator_style or []:
        if _text_contains_any(niche_text, style_keywords.get(style, [])):
            matched_styles += 1
    adjustment += min(6, matched_styles * 2)
    # TODO: Incorporate free-response preference fields via an AI scoring service.

    return max(0, min(20, adjustment))


def _build_match_score(
    card: dict[str, Any],
    filters: DiscoveryFilters,
    preferences: CompanyCreatorPreferences | None = None,
) -> int:
    score = 40
    engagement_rate = card.get("engagement_rate")
    if engagement_rate is not None:
        score += min(25, int(engagement_rate * 3.5))

    followers = card.get("followers_count") or 0
    if followers >= 10000:
        score += 10

    if filters.niche and filters.niche.lower() in card.get("niche_text", "").lower():
        score += 10

    if filters.platform and card.get("platform", "").lower() == filters.platform:
        score += 10

    if filters.audience_location and filters.audience_location.lower() in card.get("audience_location", "").lower():
        score += 5

    synced_at = card.get("synced_at")
    if synced_at and synced_at >= timezone.now() - timedelta(days=21):
        score += 10

    score += _preferences_score_adjustment(card, preferences)
    return max(0, min(100, score))


def _match_label(score: int) -> str:
    if score >= MATCH_STRONG_THRESHOLD:
        return "Strong Match"
    if score >= MATCH_POSSIBLE_THRESHOLD:
        return "Possible Match"
    return "Low Match"


def _extract_creator_data(meta: CreatorMeta) -> dict[str, Any]:
    user = meta.user
    profile_platform, _, avatar_url = meta.primary_platform_data()
    platform = (profile_platform or "instagram").strip().lower()

    connection = InstagramConnection.objects.filter(user=user).first()
    snapshot = SocialAnalyticsSnapshot.objects.filter(
        user=user,
        platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
    ).first()

    payload = snapshot.payload if snapshot and isinstance(snapshot.payload, dict) else {}
    account = payload.get("account") or {}
    engagement = payload.get("engagement") or {}
    summary = payload.get("summary_metrics") or {}
    performance = payload.get("performance") or {}
    demographics = payload.get("demographics") or {}

    followers_count = (
        int(account.get("followers_count") or 0)
        or int(getattr(connection, "followers_count", 0) or 0)
    )
    engagement_rate = _percentage(summary.get("average_engagement_rate"))
    if engagement_rate is None:
        engagement_rate = _percentage(engagement.get("engagement_rate"))

    average_reach = summary.get("average_reach")
    if average_reach in (None, ""):
        average_reach = performance.get("reach")

    gender_age = demographics.get("audience_gender_age") or []
    audience_country = demographics.get("audience_country") or []
    audience_city = demographics.get("audience_city") or []

    female_pct = _label_value(gender_age, "female")
    male_pct = _label_value(gender_age, "male")
    top_age_group = _top_demographic_label(gender_age)
    top_country = _top_demographic_label(audience_country)
    top_city = _top_demographic_label(audience_city)

    audience_location = ", ".join(part for part in [top_city, top_country] if part)
    handle = ""
    if connection and connection.instagram_username:
        handle = f"@{connection.instagram_username}"
    elif account.get("username"):
        handle = f"@{account['username']}"
    else:
        handle = f"@{user.username}"

    name = user.get_full_name() or user.username
    niche = [skill for skill in (meta.content_skills or []) if skill]

    return {
        "creator_id": user.id,
        "name": name,
        "handle": handle,
        "platform": platform,
        "platform_display": platform.title(),
        "niche": niche[:3],
        "niche_text": ", ".join(niche),
        "followers_count": followers_count,
        "engagement_rate": engagement_rate,
        "average_reach": int(average_reach or 0),
        "female_audience_pct": round(female_pct, 1) if female_pct else None,
        "male_audience_pct": round(male_pct, 1) if male_pct else None,
        "top_age_group": top_age_group,
        "top_country": top_country,
        "top_city": top_city,
        "audience_location": audience_location,
        "avatar_url": avatar_url,
        "synced_at": snapshot.synced_at if snapshot else None,
        "profile_views": performance.get("profile_visits"),
        "website_clicks": performance.get("website_clicks"),
        "average_save_rate": _percentage(summary.get("average_save_rate")),
        "average_share_rate": _percentage(summary.get("average_share_rate")),
        "average_comment_rate": _percentage(summary.get("average_comment_rate")),
    }


def _passes_filters(card: dict[str, Any], filters: DiscoveryFilters) -> bool:
    if filters.query:
        haystack = " ".join(
            [
                card.get("name", ""),
                card.get("handle", ""),
                card.get("niche_text", ""),
            ]
        ).lower()
        if filters.query.lower() not in haystack:
            return False

    if filters.platform and card.get("platform") != filters.platform:
        return False

    if filters.niche and filters.niche.lower() not in card.get("niche_text", "").lower():
        return False

    followers = card.get("followers_count") or 0
    if followers < filters.follower_min:
        return False
    if filters.follower_max is not None and followers > filters.follower_max:
        return False

    engagement_rate = card.get("engagement_rate")
    if filters.min_engagement_rate is not None:
        if engagement_rate is None or engagement_rate < filters.min_engagement_rate:
            return False

    if filters.audience_gender == "female" and not ((card.get("female_audience_pct") or 0) >= 50):
        return False
    if filters.audience_gender == "male" and not ((card.get("male_audience_pct") or 0) >= 50):
        return False

    if filters.audience_age and filters.audience_age not in (card.get("top_age_group") or "").lower():
        return False

    if filters.audience_location and filters.audience_location.lower() not in (card.get("audience_location") or "").lower():
        return False

    if filters.min_match_score is not None and (card.get("match_score") or 0) < filters.min_match_score:
        return False

    return True


def build_creator_discovery_results(
    filters: DiscoveryFilters,
    preferences: CompanyCreatorPreferences | None = None,
) -> dict[str, Any]:
    creator_qs = (
        CreatorMeta.objects.select_related("user")
        .filter(
            marketplace_enabled=True,
            user__is_creator=True,
        )
        .order_by("user__first_name", "user__last_name", "user__username")
    )

    if filters.query:
        creator_qs = creator_qs.filter(
            Q(user__first_name__icontains=filters.query)
            | Q(user__last_name__icontains=filters.query)
            | Q(user__username__icontains=filters.query)
            | Q(content_skills__icontains=filters.query)
        )

    cards: list[dict[str, Any]] = []
    niche_values: set[str] = set()
    age_bands: set[str] = set()
    location_values: set[str] = set()

    for meta in creator_qs:
        card = _extract_creator_data(meta)
        card["match_score"] = _build_match_score(card, filters, preferences)
        card["match_label"] = _match_label(card["match_score"])
        if _passes_filters(card, filters):
            cards.append(card)

        for skill in card.get("niche") or []:
            niche_values.add(skill)
        if card.get("top_age_group"):
            age_bands.add(str(card["top_age_group"]))
        if card.get("audience_location"):
            location_values.add(str(card["audience_location"]))

    cards.sort(key=lambda row: (-(row.get("match_score") or 0), -(row.get("engagement_rate") or 0), -(row.get("followers_count") or 0), row.get("name", "").lower()))

    return {
        "cards": cards,
        "available_niches": sorted(niche_values),
        "available_age_bands": sorted(age_bands),
        "available_locations": sorted(location_values),
    }
