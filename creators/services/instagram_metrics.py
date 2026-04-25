from __future__ import annotations

from statistics import median, pstdev
from typing import Any

def safe_divide(numerator: float | int | None, denominator: float | int | None, default: float = 0.0) -> float:
    if numerator is None or denominator in (None, 0):
        return default
    try:
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def _clamp(value: float | None, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if value is None:
        return 0.0
    return max(minimum, min(maximum, float(value)))


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metric_from_rows(rows: dict[str, Any], key: str) -> int | None:
    value = rows.get(key)
    return _to_int(value)


def _percent_for_label(rows: list[dict[str, Any]], matcher) -> float:
    total = sum(int(row.get("value", 0) or 0) for row in rows)
    if total <= 0:
        return 0.0
    matched = sum(int(row.get("value", 0) or 0) for row in rows if matcher(str(row.get("label") or "")))
    return safe_divide(matched, total)


def normalize_account_metrics(account: dict[str, Any], performance: dict[str, Any], missing_metrics: list[str]) -> dict[str, Any]:
    normalized = {
        "followers_count": _to_int(account.get("followers_count")),
        "follows_count": _to_int(account.get("follows_count")),
        "media_count": _to_int(account.get("media_count")),
        "account_type": account.get("account_type"),
        "reach_1d": _metric_from_rows(performance, "reach"),
        "reach_recent": _metric_from_rows(performance, "reach"),
        "follower_count_delta": _metric_from_rows(performance, "follower_count"),
        "online_followers": _metric_from_rows(performance, "online_followers"),
        "profile_views": _metric_from_rows(performance, "profile_views"),
        "website_clicks": _metric_from_rows(performance, "website_clicks"),
        "accounts_engaged": _metric_from_rows(performance, "accounts_engaged"),
        "total_interactions": _metric_from_rows(performance, "total_interactions"),
        "views": _metric_from_rows(performance, "views"),
        "follows_and_unfollows": _metric_from_rows(performance, "follows_and_unfollows"),
        "profile_links_taps": _metric_from_rows(performance, "profile_links_taps"),
    }
    for key, value in normalized.items():
        if value is None and key != "account_type":
            missing_metrics.append(key)
    return normalized


def normalize_demographics(demographics: dict[str, Any], missing_metrics: list[str]) -> dict[str, Any]:
    age_gender = demographics.get("audience_gender_age") or []
    countries = demographics.get("audience_country") or []
    cities = demographics.get("audience_city") or []

    gender_distribution: dict[str, int] = {}
    for row in age_gender:
        label = str(row.get("label") or "")
        value = int(row.get("value") or 0)
        gender = label.split(",")[0].strip() if "," in label else label.split()[0:1]
        if isinstance(gender, list):
            gender = gender[0] if gender else "unknown"
        gender_distribution[gender] = gender_distribution.get(gender, 0) + value

    us_percent = _percent_for_label(countries, lambda label: label.upper() in {"US", "USA", "UNITED STATES"})
    age_18_34_percent = _percent_for_label(
        age_gender,
        lambda label: any(token in label for token in ["18-24", "25-34"]),
    )

    top_age_groups = [row for row in age_gender[:5]]
    normalized = {
        "top_age_groups": top_age_groups,
        "gender_distribution": gender_distribution,
        "top_countries": countries[:5],
        "top_cities": cities[:5],
        "percent_us_followers": round(us_percent * 100, 2),
        "percent_target_age_18_34": round(age_18_34_percent * 100, 2),
        "dominant_city": (cities[0].get("label") if cities else None),
        "dominant_country": (countries[0].get("label") if countries else None),
        "dominant_age_group": (top_age_groups[0].get("label") if top_age_groups else None),
        "dominant_gender": (max(gender_distribution, key=gender_distribution.get) if gender_distribution else None),
    }

    if not age_gender:
        missing_metrics.append("top_age_groups")
    if not countries:
        missing_metrics.append("top_countries")
    if not cities:
        missing_metrics.append("top_cities")
    return normalized


def normalize_media_posts(media: list[dict[str, Any]], media_insights: list[dict[str, Any]], missing_metrics: list[str]) -> list[dict[str, Any]]:
    by_id = {str(item.get("id")): item for item in media if item.get("id")}
    normalized_posts: list[dict[str, Any]] = []

    for insight_row in media_insights or []:
        media_id = str(insight_row.get("media_id") or "")
        if not media_id:
            continue
        media_item = by_id.get(media_id, {})
        metric_values = {}
        for metric in insight_row.get("metrics", []) if isinstance(insight_row, dict) else []:
            name = metric.get("name")
            if name:
                metric_values[name] = int(metric.get("value") or 0)

        post = {
            "media_id": media_id,
            "media_type": (insight_row.get("media_type") or media_item.get("media_type") or "UNKNOWN"),
            "media_product_type": (insight_row.get("media_product_type") or media_item.get("media_product_type") or "UNKNOWN"),
            "timestamp": media_item.get("timestamp"),
            "likes": metric_values.get("likes", _to_int(media_item.get("like_count")) or 0),
            "comments": metric_values.get("comments", _to_int(media_item.get("comments_count")) or 0),
            "reach": metric_values.get("reach", 0),
            "saved": metric_values.get("saved", 0),
            "shares": metric_values.get("shares", 0),
            "views": metric_values.get("views", 0),
            "profile_visits": metric_values.get("profile_visits", 0),
            "profile_activity": metric_values.get("profile_activity", 0),
            "total_interactions": metric_values.get(
                "total_interactions",
                metric_values.get("likes", 0)
                + metric_values.get("comments", 0)
                + metric_values.get("saved", 0)
                + metric_values.get("shares", 0),
            ),
        }
        normalized_posts.append(post)

    if not normalized_posts:
        missing_metrics.append("media_insights")

    return normalized_posts


def add_post_rates(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rated_posts: list[dict[str, Any]] = []
    for post in posts:
        reach = post.get("reach") or 0
        rated_post = dict(post)
        rated_post.update(
            {
                "engagement_rate": safe_divide(post.get("total_interactions"), reach),
                "like_rate": safe_divide(post.get("likes"), reach),
                "comment_rate": safe_divide(post.get("comments"), reach),
                "save_rate": safe_divide(post.get("saved"), reach),
                "share_rate": safe_divide(post.get("shares"), reach),
                "view_to_reach_ratio": safe_divide(post.get("views"), reach),
                "profile_visit_rate": safe_divide(post.get("profile_visits"), reach),
            }
        )
        rated_posts.append(rated_post)
    return rated_posts


def _audience_match_score(audience: dict[str, Any], target_filters: dict[str, Any] | None = None) -> float:
    if not target_filters:
        return _clamp((audience.get("percent_us_followers", 0) / 100.0 + audience.get("percent_target_age_18_34", 0) / 100.0) / 2)

    score = 0.0
    checks = 0
    target_country = target_filters.get("target_country")
    if target_country:
        checks += 1
        score += 1.0 if str(audience.get("dominant_country") or "").lower() == str(target_country).lower() else 0.0

    target_city = target_filters.get("target_city")
    if target_city:
        checks += 1
        score += 1.0 if str(audience.get("dominant_city") or "").lower() == str(target_city).lower() else 0.0

    target_gender = target_filters.get("target_gender")
    if target_gender:
        checks += 1
        score += 1.0 if str(audience.get("dominant_gender") or "").lower() == str(target_gender).lower() else 0.0

    age_min = target_filters.get("target_age_min")
    age_max = target_filters.get("target_age_max")
    if age_min is not None or age_max is not None:
        checks += 1
        score += audience.get("percent_target_age_18_34", 0) / 100.0

    return _clamp(score / checks if checks else 0.5)


def calculate_creator_metrics(posts: list[dict[str, Any]], followers_count: int | None, audience: dict[str, Any], target_filters: dict[str, Any] | None = None) -> dict[str, Any]:
    if not posts:
        return {
            "average_post_reach": 0,
            "average_post_views": 0,
            "average_engagement_rate": 0.0,
            "average_save_rate": 0.0,
            "average_share_rate": 0.0,
            "average_comment_rate": 0.0,
            "average_profile_visit_rate": 0.0,
            "median_post_reach": 0,
            "best_performing_post": None,
            "worst_performing_post": None,
            "content_consistency_score": 0.0,
            "hidden_gem_score": 0.0,
            "audience_match_score": _audience_match_score(audience, target_filters),
            "creator_value_score": 0.0,
        }

    reaches = [int(post.get("reach") or 0) for post in posts]
    views = [int(post.get("views") or 0) for post in posts]

    avg_engagement_rate = sum(post.get("engagement_rate", 0.0) for post in posts) / len(posts)
    avg_save_rate = sum(post.get("save_rate", 0.0) for post in posts) / len(posts)
    avg_share_rate = sum(post.get("share_rate", 0.0) for post in posts) / len(posts)
    avg_comment_rate = sum(post.get("comment_rate", 0.0) for post in posts) / len(posts)
    avg_profile_visit_rate = sum(post.get("profile_visit_rate", 0.0) for post in posts) / len(posts)

    avg_reach = sum(reaches) / len(reaches)
    stdev = pstdev(reaches) if len(reaches) > 1 else 0
    coeff_var = safe_divide(stdev, avg_reach)
    content_consistency_score = round(max(0.0, min(100.0, (1 - coeff_var) * 100)), 2)

    followers = max(int(followers_count or 0), 1)
    engagement_per_follower = avg_engagement_rate
    scale_bonus = 1 - _clamp(followers / 200000.0)
    hidden_gem_score = round(max(0.0, min(100.0, ((engagement_per_follower * 100) * 0.7 + scale_bonus * 100 * 0.3))), 2)

    audience_match_score = round(_audience_match_score(audience, target_filters) * 100, 2)

    profile_visit_component = _clamp(avg_profile_visit_rate)
    creator_value_score = round(
        max(
            0.0,
            min(
                100.0,
                (
                    _clamp(avg_engagement_rate) * 0.35
                    + _clamp(avg_save_rate) * 0.20
                    + _clamp(avg_share_rate) * 0.15
                    + _clamp(audience_match_score / 100.0) * 0.15
                    + _clamp(content_consistency_score / 100.0) * 0.10
                    + profile_visit_component * 0.05
                )
                * 100,
            ),
        ),
        2,
    )

    ranked = sorted(posts, key=lambda post: (post.get("engagement_rate", 0), post.get("reach", 0)), reverse=True)

    return {
        "average_post_reach": round(avg_reach, 2),
        "average_post_views": round(sum(views) / len(views), 2),
        "average_engagement_rate": round(avg_engagement_rate, 4),
        "average_save_rate": round(avg_save_rate, 4),
        "average_share_rate": round(avg_share_rate, 4),
        "average_comment_rate": round(avg_comment_rate, 4),
        "average_profile_visit_rate": round(avg_profile_visit_rate, 4),
        "median_post_reach": int(median(reaches)),
        "best_performing_post": ranked[0],
        "worst_performing_post": ranked[-1],
        "content_consistency_score": content_consistency_score,
        "hidden_gem_score": hidden_gem_score,
        "audience_match_score": audience_match_score,
        "creator_value_score": creator_value_score,
    }


def build_labels(metrics: dict[str, Any], audience: dict[str, Any], followers_count: int | None, missing_metrics: list[str]) -> list[str]:
    labels: list[str] = []

    if metrics.get("average_engagement_rate", 0) >= 0.08:
        labels.append("High engagement creator")
    if audience.get("percent_us_followers", 0) >= 50:
        labels.append("Strong local audience")
    if audience.get("percent_target_age_18_34", 0) >= 45:
        labels.append("Strong 18–24 audience")
    if metrics.get("average_share_rate", 0) >= 0.03:
        labels.append("High shareability")
    if metrics.get("average_profile_visit_rate", 0) >= 0.02:
        labels.append("High profile conversion potential")
    if metrics.get("average_post_reach", 0) < 100:
        labels.append("Low recent activity")

    followers = followers_count or 0
    if followers < 10000 and metrics.get("average_engagement_rate", 0) >= 0.08:
        labels.append("Small audience, strong engagement")
    if followers >= 100000 and metrics.get("average_engagement_rate", 0) < 0.03:
        labels.append("Large audience, low engagement risk")

    if len(missing_metrics) >= 4:
        labels.append("Insufficient data")

    return labels


def build_insight_cards(metrics: dict[str, Any], audience: dict[str, Any], account_metrics: dict[str, Any], missing_metrics: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []

    if metrics.get("average_engagement_rate", 0) >= 0.08:
        cards.append(
            {
                "title": "High engagement efficiency",
                "severity": "positive",
                "description": "This creator has a high engagement rate compared with their audience size.",
                "supporting_metric": "average_engagement_rate",
                "value": round(metrics.get("average_engagement_rate", 0) * 100, 2),
            }
        )

    if audience.get("dominant_city") and audience.get("dominant_country"):
        cards.append(
            {
                "title": "Local audience concentration",
                "severity": "positive" if audience.get("percent_us_followers", 0) >= 40 else "neutral",
                "description": f"Most followers are concentrated in {audience.get('dominant_city')}, {audience.get('dominant_country')}, making this creator useful for local campaigns.",
                "supporting_metric": "dominant_city",
                "value": audience.get("dominant_city"),
            }
        )

    followers = account_metrics.get("followers_count") or 0
    if followers:
        reach_ratio = safe_divide(metrics.get("average_post_reach", 0), followers)
        cards.append(
            {
                "title": "Reach productivity",
                "severity": "positive" if reach_ratio >= 0.3 else "neutral",
                "description": "Posts generate strong reach relative to follower count." if reach_ratio >= 0.3 else "Posts are generating moderate reach relative to follower count.",
                "supporting_metric": "average_post_reach_to_followers_ratio",
                "value": round(reach_ratio, 3),
            }
        )

    profile_data_limited = account_metrics.get("profile_views") is None and account_metrics.get("profile_links_taps") is None
    if missing_metrics or profile_data_limited:
        cards.append(
            {
                "title": "Partial data coverage",
                "severity": "warning" if profile_data_limited else "neutral",
                "description": "Audience data is available, but recent profile activity metrics are limited.",
                "supporting_metric": "missing_metrics_count",
                "value": len(missing_metrics),
            }
        )

    return cards[:6]


def build_data_quality(profile: dict[str, Any], audience: dict[str, Any], posts: list[dict[str, Any]], missing_metrics: list[str]) -> dict[str, Any]:
    warnings: list[str] = []
    if "reach_1d" in missing_metrics:
        warnings.append("Account reach metric is unavailable.")
    if "media_insights" in missing_metrics:
        warnings.append("Media insights are unavailable for recent posts.")

    return {
        "has_profile_data": bool(profile.get("username") or profile.get("followers_count") is not None),
        "has_demographics": bool(audience.get("top_countries") or audience.get("top_age_groups")),
        "has_media_insights": bool(posts),
        "missing_metrics": sorted(set(missing_metrics)),
        "warnings": warnings,
    }


def build_dashboard_payload(
    profile: dict[str, Any],
    account_metrics: dict[str, Any],
    audience: dict[str, Any],
    posts: list[dict[str, Any]],
    creator_metrics: dict[str, Any],
    labels: list[str],
    insight_cards: list[dict[str, Any]],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    top_posts = sorted(posts, key=lambda post: post.get("engagement_rate", 0), reverse=True)[:5]
    return {
        "profile": profile,
        "summary_metrics": creator_metrics,
        "audience": audience,
        "content_performance": {
            "posts_analyzed": len(posts),
            "average_post_reach": creator_metrics.get("average_post_reach", 0),
            "average_post_views": creator_metrics.get("average_post_views", 0),
        },
        "top_posts": top_posts,
        "insight_cards": insight_cards,
        "creator_value_score": creator_metrics.get("creator_value_score", 0),
        "recommendation_labels": labels,
        "data_quality": data_quality,
        "account_metrics": account_metrics,
    }
