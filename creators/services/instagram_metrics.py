from __future__ import annotations

from statistics import median as statistics_median, pstdev
from typing import Any


def safe_divide(
    numerator: float | int | None,
    denominator: float | int | None,
    default: float | None = None,
) -> float | None:
    if numerator is None or denominator in (None, 0):
        return default
    try:
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def percentage(value: float | int | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * 100, digits)
    except (TypeError, ValueError):
        return None


def mean(values: list[float | int | None]) -> float | None:
    cleaned = [float(v) for v in values if isinstance(v, (int, float))]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def median(values: list[float | int | None]) -> float | None:
    cleaned = [float(v) for v in values if isinstance(v, (int, float))]
    if not cleaned:
        return None
    return float(statistics_median(cleaned))


def standard_deviation(values: list[float | int | None]) -> float:
    cleaned = [float(v) for v in values if isinstance(v, (int, float))]
    if len(cleaned) < 2:
        return 0.0
    return float(pstdev(cleaned))


def _clamp(value: float | None, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if value is None:
        return minimum
    return max(minimum, min(maximum, float(value)))


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metric_from_rows(rows: dict[str, Any], key: str) -> int | None:
    return _to_int(rows.get(key))


def _percent_for_label(rows: list[dict[str, Any]], matcher) -> float | None:
    total = sum(int(row.get("value", 0) or 0) for row in rows)
    if total <= 0:
        return None
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
        gender = label.split(",")[0].strip().lower() if "," in label else "unknown"
        gender_distribution[gender] = gender_distribution.get(gender, 0) + value

    total_country = sum(int(row.get("value") or 0) for row in countries)
    total_age_gender = sum(int(row.get("value") or 0) for row in age_gender)
    total_city = sum(int(row.get("value") or 0) for row in cities)
    denominator = max(total_country, total_age_gender, total_city)

    percent_us = _percent_for_label(countries, lambda label: label.upper() in {"US", "USA", "UNITED STATES"})
    percent_18_34 = _percent_for_label(age_gender, lambda label: "18-24" in label or "25-34" in label)
    percent_top_city = safe_divide(int(cities[0].get("value") or 0), denominator) if cities and denominator else None

    city_shares = [safe_divide(int(row.get("value") or 0), total_city) for row in cities] if total_city else []
    hhi = sum((share or 0) ** 2 for share in city_shares)

    top_age_group = (age_gender[0].get("label") if age_gender else None)
    top_gender = max(gender_distribution, key=gender_distribution.get) if gender_distribution else None
    top_country = (countries[0].get("label") if countries else None)
    top_city = (cities[0].get("label") if cities else None)

    normalized = {
        "top_age_groups": age_gender[:5],
        "gender_distribution": gender_distribution,
        "top_countries": countries[:5],
        "top_cities": cities[:5],
        "top_city": top_city,
        "top_country": top_country,
        "top_age_group": top_age_group,
        "top_gender": top_gender,
        "percent_us_followers": percentage(percent_us),
        "percent_top_city_followers": percentage(percent_top_city),
        "percent_18_34_followers": percentage(percent_18_34),
        "audience_concentration_score": round(_clamp(hhi) * 100, 2) if city_shares else 0.0,
        "local_audience_strength": round(_clamp(percent_top_city) * 100, 2) if percent_top_city is not None else 0.0,
    }

    if not age_gender:
        missing_metrics.append("audience_gender_age")
    if not countries:
        missing_metrics.append("audience_country")
    if not cities:
        missing_metrics.append("audience_city")
    return normalized


def normalize_media_posts(media: list[dict[str, Any]], media_insights: list[dict[str, Any]], missing_metrics: list[str]) -> list[dict[str, Any]]:
    media_by_id = {str(item.get("id")): item for item in media if item.get("id")}
    normalized_posts: list[dict[str, Any]] = []

    for insight_row in media_insights or []:
        media_id = str(insight_row.get("media_id") or "")
        if not media_id:
            continue
        media_item = media_by_id.get(media_id, {})
        metric_values: dict[str, int] = {}
        for metric in insight_row.get("metrics", []) if isinstance(insight_row, dict) else []:
            name = metric.get("name")
            if name:
                metric_values[name] = int(metric.get("value") or 0)

        likes = metric_values.get("likes", _to_int(media_item.get("like_count")) or 0)
        comments = metric_values.get("comments", _to_int(media_item.get("comments_count")) or 0)
        saves = metric_values.get("saved", 0)
        shares = metric_values.get("shares", 0)

        normalized_posts.append(
            {
                "media_id": media_id,
                "media_type": (insight_row.get("media_type") or media_item.get("media_type") or "UNKNOWN"),
                "media_product_type": (insight_row.get("media_product_type") or media_item.get("media_product_type") or "UNKNOWN"),
                "timestamp": insight_row.get("timestamp") or media_item.get("timestamp"),
                "post_date": insight_row.get("timestamp") or media_item.get("timestamp"),
                "thumbnail_url": insight_row.get("thumbnail_url") or insight_row.get("media_url") or media_item.get("thumbnail_url") or media_item.get("media_url") or "",
                "permalink": insight_row.get("permalink") or media_item.get("permalink") or "",
                "likes": likes,
                "comments": comments,
                "saved": saves,
                "shares": shares,
                "reach": metric_values.get("reach", 0),
                "views": metric_values.get("views", 0),
                "profile_visits": metric_values.get("profile_visits", 0),
                "total_interactions": metric_values.get("total_interactions", likes + comments + saves + shares),
            }
        )

    if not normalized_posts:
        missing_metrics.append("media_insights")

    return normalized_posts


def _normalized_rate_for_score(rate: float | None, target: float) -> float:
    if rate is None or target <= 0:
        return 0.0
    return _clamp(rate / target)


def add_post_rates(posts: list[dict[str, Any]], content_consistency_score: float | None = None) -> list[dict[str, Any]]:
    rated_posts: list[dict[str, Any]] = []
    for post in posts:
        reach = post.get("reach")
        views = post.get("views")
        likes = int(post.get("likes") or 0)
        comments = int(post.get("comments") or 0)
        saves = int(post.get("saved") or 0)
        shares = int(post.get("shares") or 0)
        profile_visits = int(post.get("profile_visits") or 0)
        engagement = likes + comments + saves + shares
        engagement_rate = safe_divide(engagement, reach)
        like_rate = safe_divide(likes, reach)
        comment_rate = safe_divide(comments, reach)
        save_rate = safe_divide(saves, reach)
        share_rate = safe_divide(shares, reach)
        view_rate = safe_divide(views, reach)
        profile_visit_rate = safe_divide(profile_visits, reach)
        virality_rate = safe_divide(shares, views)
        content_value_rate = safe_divide(saves, views)
        engagement_quality_rate = safe_divide(likes + (comments * 2) + (saves * 3) + (shares * 4), reach)
        conversion_potential_rate = profile_visit_rate
        brand_discovery_rate = mean([share_rate, save_rate, profile_visit_rate])

        standardized_performance_score = round(
            (
                _normalized_rate_for_score(engagement_rate, 0.08) * 0.35
                + _normalized_rate_for_score(save_rate, 0.04) * 0.20
                + _normalized_rate_for_score(share_rate, 0.03) * 0.20
                + _normalized_rate_for_score(profile_visit_rate, 0.03) * 0.15
                + _normalized_rate_for_score(view_rate, 1.2) * 0.10
            )
            * 100,
            2,
        )

        rated_post = dict(post)
        rated_post.update(
            {
                "engagement": engagement,
                "engagement_rate": engagement_rate,
                "like_rate": like_rate,
                "comment_rate": comment_rate,
                "save_rate": save_rate,
                "share_rate": share_rate,
                "view_rate": view_rate,
                "profile_visit_rate": profile_visit_rate,
                "virality_rate": virality_rate,
                "content_value_rate": content_value_rate,
                "engagement_quality_score": round(_normalized_rate_for_score(engagement_quality_rate, 0.2) * 100, 2),
                "conversion_potential_score": round(_normalized_rate_for_score(conversion_potential_rate, 0.03) * 100, 2),
                "brand_discovery_score": round(_normalized_rate_for_score(brand_discovery_rate, 0.03) * 100, 2),
                "raw_activity_score": (int(views or 0) * 2) + (engagement * 3),
                "standardized_performance_score": standardized_performance_score,
                "consistency_component": content_consistency_score,
            }
        )
        rated_posts.append(rated_post)

    rated_posts.sort(key=lambda row: (row.get("standardized_performance_score") or 0, row.get("engagement") or 0), reverse=True)
    return rated_posts


def _audience_match_score(audience: dict[str, Any], target_filters: dict[str, Any] | None = None) -> float:
    if not target_filters:
        us = (audience.get("percent_us_followers") or 0) / 100.0
        age = (audience.get("percent_18_34_followers") or 0) / 100.0
        return _clamp((us + age) / 2)
    return 0.5


def _best_post(posts: list[dict[str, Any]], metric_name: str) -> dict[str, Any] | None:
    valid = [post for post in posts if isinstance(post.get(metric_name), (int, float))]
    if not valid:
        return None
    return max(valid, key=lambda post: post.get(metric_name, 0))


def calculate_creator_metrics(posts: list[dict[str, Any]], followers_count: int | None, audience: dict[str, Any], target_filters: dict[str, Any] | None = None) -> dict[str, Any]:
    if not posts:
        return {
            "average_engagement_rate": None,
            "average_like_rate": None,
            "average_comment_rate": None,
            "average_save_rate": None,
            "average_share_rate": None,
            "average_profile_visit_rate": None,
            "average_reach": 0,
            "median_reach": 0,
            "average_views": 0,
            "best_post_by_engagement_rate": None,
            "best_post_by_share_rate": None,
            "best_post_by_profile_visit_rate": None,
            "content_consistency_score": 0.0,
            "engagement_quality_score": 0.0,
            "hidden_gem_score": 0.0,
            "conversion_potential_score": 0.0,
            "brand_discovery_score": 0.0,
            "standardized_performance_score": 0.0,
            "audience_match_score": round(_audience_match_score(audience, target_filters) * 100, 2),
        }

    reaches = [post.get("reach") for post in posts]
    reach_mean = mean(reaches) or 0.0
    reach_std = standard_deviation(reaches)
    reach_cv = safe_divide(reach_std, reach_mean, default=1.0)
    content_consistency_score = round((1 - _clamp(reach_cv)) * 100, 2)

    posts_with_consistency = add_post_rates(posts, content_consistency_score=content_consistency_score)

    avg_engagement_rate = mean([post.get("engagement_rate") for post in posts_with_consistency])
    avg_like_rate = mean([post.get("like_rate") for post in posts_with_consistency])
    avg_comment_rate = mean([post.get("comment_rate") for post in posts_with_consistency])
    avg_save_rate = mean([post.get("save_rate") for post in posts_with_consistency])
    avg_share_rate = mean([post.get("share_rate") for post in posts_with_consistency])
    avg_profile_visit_rate = mean([post.get("profile_visit_rate") for post in posts_with_consistency])
    avg_view_rate = mean([post.get("view_rate") for post in posts_with_consistency])

    followers = max(int(followers_count or 0), 1)
    scale_bonus = 1 - _clamp(followers / 200000.0)
    hidden_gem_score = round(
        (
            _normalized_rate_for_score(avg_engagement_rate, 0.08) * 0.75
            + scale_bonus * 0.25
        )
        * 100,
        2,
    )

    standardized_performance_score = round(
        (
            _normalized_rate_for_score(avg_engagement_rate, 0.08) * 0.35
            + _normalized_rate_for_score(avg_save_rate, 0.04) * 0.20
            + _normalized_rate_for_score(avg_share_rate, 0.03) * 0.20
            + _normalized_rate_for_score(avg_profile_visit_rate, 0.03) * 0.15
            + _normalized_rate_for_score(avg_view_rate, 1.2) * 0.10
        )
        * 100,
        2,
    )

    return {
        "average_engagement_rate": avg_engagement_rate,
        "average_like_rate": avg_like_rate,
        "average_comment_rate": avg_comment_rate,
        "average_save_rate": avg_save_rate,
        "average_share_rate": avg_share_rate,
        "average_profile_visit_rate": avg_profile_visit_rate,
        "average_reach": round(reach_mean, 2),
        "median_reach": int(median(reaches) or 0),
        "average_views": round(mean([post.get("views") for post in posts_with_consistency]) or 0, 2),
        "best_post_by_engagement_rate": _best_post(posts_with_consistency, "engagement_rate"),
        "best_post_by_share_rate": _best_post(posts_with_consistency, "share_rate"),
        "best_post_by_profile_visit_rate": _best_post(posts_with_consistency, "profile_visit_rate"),
        "content_consistency_score": content_consistency_score,
        "engagement_quality_score": round(mean([post.get("engagement_quality_score") for post in posts_with_consistency]) or 0, 2),
        "hidden_gem_score": hidden_gem_score,
        "conversion_potential_score": round(mean([post.get("conversion_potential_score") for post in posts_with_consistency]) or 0, 2),
        "brand_discovery_score": round(mean([post.get("brand_discovery_score") for post in posts_with_consistency]) or 0, 2),
        "standardized_performance_score": standardized_performance_score,
        "audience_match_score": round(_audience_match_score(audience, target_filters) * 100, 2),
        "posts": posts_with_consistency,
    }


def build_labels(metrics: dict[str, Any], audience: dict[str, Any], followers_count: int | None, missing_metrics: list[str]) -> list[str]:
    labels: list[str] = []
    if (metrics.get("average_engagement_rate") or 0) >= 0.08:
        labels.append("High engagement creator")
    if (metrics.get("average_share_rate") or 0) >= 0.03:
        labels.append("High shareability")
    if (audience.get("percent_us_followers") or 0) >= 40:
        labels.append("Strong US audience")
    if (audience.get("percent_18_34_followers") or 0) >= 45:
        labels.append("Strong 18-34 audience")
    if followers_count and followers_count < 10000 and (metrics.get("average_engagement_rate") or 0) >= 0.08:
        labels.append("Small audience, strong engagement")
    if len(missing_metrics) >= 4:
        labels.append("Insufficient data")
    return labels


def build_insight_cards(metrics: dict[str, Any], audience: dict[str, Any], account_metrics: dict[str, Any], missing_metrics: list[str]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []

    avg_engagement_rate = metrics.get("average_engagement_rate")
    avg_share_rate = metrics.get("average_share_rate")
    avg_save_rate = metrics.get("average_save_rate")
    avg_profile_visit_rate = metrics.get("average_profile_visit_rate")

    if avg_engagement_rate is not None and avg_engagement_rate >= 0.06:
        cards.append({
            "title": "Strong Engagement Rate",
            "type": "positive",
            "description": "Recent posts are generating strong interactions per account reached.",
            "metric_name": "average_engagement_rate",
            "metric_value": percentage(avg_engagement_rate),
        })

    if avg_share_rate is not None and avg_share_rate >= 0.02:
        cards.append({
            "title": "High Shareability",
            "type": "positive",
            "description": "People are sharing content at a healthy rate, supporting organic discovery.",
            "metric_name": "average_share_rate",
            "metric_value": percentage(avg_share_rate),
        })

    if avg_save_rate is not None and avg_save_rate < 0.01:
        cards.append({
            "title": "Limited Save Activity",
            "type": "warning",
            "description": "Save behavior is lower than typical benchmarks for evergreen content.",
            "metric_name": "average_save_rate",
            "metric_value": percentage(avg_save_rate),
        })

    if avg_profile_visit_rate is not None and avg_profile_visit_rate >= 0.015:
        cards.append({
            "title": "Good Profile Visit Conversion",
            "type": "positive",
            "description": "Content is converting reached viewers into profile visits.",
            "metric_name": "average_profile_visit_rate",
            "metric_value": percentage(avg_profile_visit_rate),
        })

    if audience.get("top_city"):
        cards.append({
            "title": "Audience Concentrated in %s" % audience.get("top_city"),
            "type": "neutral",
            "description": "Top city represents a meaningful part of the audience distribution.",
            "metric_name": "percent_top_city_followers",
            "metric_value": audience.get("percent_top_city_followers"),
        })

    if (audience.get("local_audience_strength") or 0) >= 25:
        cards.append({
            "title": "Strong Local Audience",
            "type": "positive",
            "description": "A strong share of followers come from one city, useful for local activations.",
            "metric_name": "local_audience_strength",
            "metric_value": audience.get("local_audience_strength"),
        })

    if missing_metrics or not metrics.get("posts"):
        cards.append({
            "title": "Not Enough Recent Post Data",
            "type": "warning",
            "description": "Some Instagram insights are missing, so certain metrics may be incomplete.",
            "metric_name": "missing_metrics",
            "metric_value": len(missing_metrics),
        })

    return cards[:8]


def build_data_quality(profile: dict[str, Any], audience: dict[str, Any], posts: list[dict[str, Any]], missing_metrics: list[str]) -> dict[str, Any]:
    warnings: list[str] = []
    if "media_insights" in missing_metrics:
        warnings.append("Recent media insights were empty or unavailable from Meta.")
    if "audience_country" in missing_metrics or "audience_city" in missing_metrics:
        warnings.append("Audience demographic coverage is partial.")
    if not profile.get("username"):
        warnings.append("Profile metadata is incomplete.")

    return {
        "has_profile_data": bool(profile.get("username") or profile.get("followers_count") is not None),
        "has_demographics": bool(audience.get("top_countries") or audience.get("top_cities") or audience.get("top_age_groups")),
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
    top_posts = sorted(posts, key=lambda post: post.get("standardized_performance_score") or 0, reverse=True)[:5]
    return {
        "profile": profile,
        "summary_metrics": creator_metrics,
        "audience": audience,
        "content_performance": {
            "posts_analyzed": len(posts),
            "average_reach": creator_metrics.get("average_reach", 0),
            "average_views": creator_metrics.get("average_views", 0),
        },
        "top_posts": top_posts,
        "insight_cards": insight_cards,
        "standardized_performance_score": creator_metrics.get("standardized_performance_score", 0),
        "recommendation_labels": labels,
        "data_quality": data_quality,
        "account_metrics": account_metrics,
    }
