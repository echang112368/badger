from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone

from creators.models import SocialAnalyticsSnapshot
from instagram_connect.models import InstagramConnection
from instagram_connect.services import get_instagram_api_base, get_instagram_user
from .instagram_metrics import (
    add_post_rates,
    build_dashboard_payload,
    build_data_quality,
    build_insight_cards,
    build_labels,
    calculate_creator_metrics,
    normalize_account_metrics,
    normalize_demographics,
    normalize_media_posts,
)


REQUEST_TIMEOUT_SECONDS = 15
GRAPH_BASE_URL = get_instagram_api_base()
INSTAGRAM_LOGIN_INSIGHTS_BASE_URL = "https://graph.instagram.com/v25.0"
INSTAGRAM_DEBUG_LOGGING = getattr(settings, "INSTAGRAM_DEBUG_LOGGING", False)

ACCOUNT_INSIGHT_METRICS = [
    "reach",
    "follower_count",
    "online_followers",
    "profile_views",
    "website_clicks",
    "accounts_engaged",
    "total_interactions",
    "views",
    "follows_and_unfollows",
    "profile_links_taps",
]

DEMOGRAPHIC_INSIGHT_REQUESTS = [
    {"breakdown": "age,gender"},
    {"breakdown": "country"},
    {"breakdown": "city"},
]

MEDIA_INSIGHT_METRICS_BY_PRODUCT_TYPE = {
    "FEED": [
        "comments",
        "likes",
        "profile_activity",
        "profile_visits",
        "reach",
        "saved",
        "shares",
        "total_interactions",
        "views",
    ],
    "REELS": [
        "comments",
        "likes",
        "reach",
        "saved",
        "shares",
        "total_interactions",
        "views",
        "ig_reels_avg_watch_time",
        "ig_reels_video_view_total_time",
        "reels_skip_rate",
        "crossposted_views",
        "facebook_views",
    ],
    "STORY": [
        "facebook_views",
        "follows",
        "navigation",
        "profile_activity",
        "profile_visits",
        "reach",
        "replies",
        "shares",
        "total_interactions",
        "views",
    ],
}

BREAKDOWN_INSIGHT_REQUESTS = {
    "FEED": [("profile_activity", "action_type")],
    "REELS": [],
    "STORY": [
        ("navigation", "story_navigation_action_type"),
        ("profile_activity", "action_type"),
    ],
}


@dataclass
class PlatformDashboardData:
    slug: str
    name: str
    connected: bool
    can_connect: bool
    connect_url: str
    refreshed: bool
    last_synced_at: datetime | None
    metrics: dict[str, Any]


class InstagramAnalyticsService:
    platform = SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM

    def __init__(self, user):
        self.user = user
        self.failed_requests: list[dict[str, Any]] = []

    def build_platform_data(self, refresh: bool = False) -> PlatformDashboardData:
        connection = getattr(self.user, "instagram_connection", None)
        if not connection:
            return PlatformDashboardData(
                slug=self.platform,
                name="Instagram",
                connected=False,
                can_connect=True,
                connect_url="/instagram/connect/",
                refreshed=False,
                last_synced_at=None,
                metrics=self.empty_metrics(),
            )

        snapshot, _ = SocialAnalyticsSnapshot.objects.get_or_create(
            user=self.user,
            platform=self.platform,
            defaults={"payload": self.empty_metrics()},
        )

        payload = snapshot.payload or self.empty_metrics()
        refreshed = False

        if refresh:
            payload = self.fetch_and_cache(connection, snapshot)
            refreshed = True

        metrics = self.normalize_payload(connection, payload)
        return PlatformDashboardData(
            slug=self.platform,
            name="Instagram",
            connected=True,
            can_connect=True,
            connect_url="/instagram/connect/",
            refreshed=refreshed,
            last_synced_at=connection.last_synced_at or snapshot.synced_at,
            metrics=metrics,
        )

    def fetch_and_cache(
        self,
        connection: InstagramConnection,
        snapshot: SocialAnalyticsSnapshot | None = None,
    ) -> dict[str, Any]:
        now = timezone.now()
        self.failed_requests = []
        ig_user_id = self._resolve_ig_user_id(connection)
        payload = self.empty_metrics()
        payload["account"] = self.fetch_account(connection, ig_user_id)
        payload["performance"] = self.fetch_account_performance(connection, ig_user_id)
        payload["demographics"] = self.fetch_demographics(connection, ig_user_id)

        recent_media = self.fetch_recent_media(connection, ig_user_id, limit=20)
        payload["recent_media"] = recent_media
        media_insights = self.fetch_media_insights(connection, recent_media)
        payload["media_insights"] = media_insights
        payload["content_performance"] = self._build_content_performance_rows(media_insights)
        payload["engagement"] = self.fetch_engagement_metrics(media_insights)
        payload["story"] = self.fetch_story_metrics(media_insights)
        payload["comments"] = self.fetch_comments(connection, recent_media)
        payload["failed_requests"] = list(self.failed_requests)
        payload["synced_at"] = now.isoformat()

        if snapshot is not None:
            snapshot.payload = payload
            snapshot.save(update_fields=["payload", "synced_at"])

        connection.last_synced_at = now
        connection.instagram_user_id = ig_user_id
        connection.save(update_fields=["last_synced_at", "instagram_user_id"])
        return payload

    def fetch_account(self, connection: InstagramConnection, ig_user_id: str) -> dict[str, Any]:
        fields = "id,username,biography,followers_count,follows_count,media_count,account_type"
        return self._get_with_fallback(
            [f"{GRAPH_BASE_URL}/{ig_user_id}", f"{GRAPH_BASE_URL}/me"],
            {
                "fields": fields,
                "access_token": connection.instagram_access_token,
            },
        )

    def fetch_account_performance(
        self, connection: InstagramConnection, ig_user_id: str
    ) -> dict[str, int | None]:
        metrics = {metric_name: None for metric_name in ACCOUNT_INSIGHT_METRICS}
        for metric_name in list(metrics.keys()):
            metrics[metric_name] = self.fetch_single_account_metric(
                connection,
                ig_user_id,
                metric_name,
            )
        return metrics

    def fetch_single_account_metric(
        self,
        connection: InstagramConnection,
        ig_user_id: str,
        metric: str,
    ) -> int | None:
        payload = self._safe_json_get(
            f"{GRAPH_BASE_URL}/{ig_user_id}/insights",
            {
                "metric": metric,
                "period": "day",
                "access_token": connection.instagram_access_token,
            },
        )
        if not payload:
            return None
        return self._extract_metric_value(payload)

    def fetch_demographics(
        self,
        connection: InstagramConnection,
        ig_user_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        rows_by_breakdown: dict[str, list[dict[str, Any]]] = {}
        for request in DEMOGRAPHIC_INSIGHT_REQUESTS:
            breakdown = request["breakdown"]
            rows_by_breakdown[breakdown] = self.fetch_demographic_breakdown(
                connection,
                ig_user_id,
                breakdown=breakdown,
            )
        return {
            "audience_gender_age": rows_by_breakdown.get("age,gender", []),
            "audience_country": rows_by_breakdown.get("country", []),
            "audience_city": rows_by_breakdown.get("city", []),
        }

    def fetch_demographic_breakdown(
        self,
        connection: InstagramConnection,
        ig_user_id: str,
        breakdown: str,
    ) -> list[dict[str, Any]]:
        payload = self._safe_json_get(
            f"{GRAPH_BASE_URL}/{ig_user_id}/insights",
            {
                "metric": "follower_demographics",
                "period": "lifetime",
                "breakdown": breakdown,
                "metric_type": "total_value",
                "access_token": connection.instagram_access_token,
            },
        )
        return self._extract_breakdown_rows(payload)

    def fetch_recent_media(
        self,
        connection: InstagramConnection,
        ig_user_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        media_payload = self._safe_json_get(
            f"{GRAPH_BASE_URL}/{ig_user_id}/media",
            {
                "fields": "id,media_type,media_product_type,like_count,comments_count,timestamp,media_url,thumbnail_url,permalink",
                "limit": limit,
                "access_token": connection.instagram_access_token,
            },
        )
        data = media_payload.get("data") if isinstance(media_payload, dict) else []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def fetch_media_insights(
        self,
        connection: InstagramConnection,
        media: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in media:
            media_id = item.get("id")
            if not media_id:
                continue
            rows.append(
                {
                    "media_id": str(media_id),
                    "media_type": str(item.get("media_type") or "").upper(),
                    "media_product_type": str(item.get("media_product_type") or "").upper(),
                    "timestamp": item.get("timestamp"),
                    "thumbnail_url": item.get("thumbnail_url") or item.get("media_url") or "",
                    "permalink": item.get("permalink") or "",
                    "metrics": self._fetch_media_insights_for_item(connection, str(media_id), item),
                }
            )
        return rows

    def fetch_engagement_metrics(self, media_insights: list[dict[str, Any]]) -> dict[str, int]:
        totals = {
            "likes": 0,
            "comments": 0,
            "saved": 0,
            "shares": 0,
            "views": 0,
        }
        for item in media_insights:
            for metric in item.get("metrics", []) if isinstance(item, dict) else []:
                name = metric.get("name")
                value = int(metric.get("value") or 0)
                if name == "likes":
                    totals["likes"] += value
                elif name == "comments":
                    totals["comments"] += value
                elif name == "saved":
                    totals["saved"] += value
                elif name == "shares":
                    totals["shares"] += value
                elif name == "views":
                    totals["views"] += value

        return totals

    def fetch_story_metrics(
        self,
        media_insights: list[dict[str, Any]],
    ) -> dict[str, int]:
        metrics = {
            "views": 0,
            "reach": 0,
            "replies": 0,
            "shares": 0,
            "profile_visits": 0,
            "follows": 0,
        }
        for item in media_insights:
            if str(item.get("media_type") or "").upper() != "STORY":
                continue
            for metric in item.get("metrics", []) if isinstance(item, dict) else []:
                name = metric.get("name")
                if name in metrics:
                    metrics[name] += int(metric.get("value") or 0)
        return metrics

    def _fetch_media_insights_for_item(
        self,
        connection: InstagramConnection,
        media_id: str,
        media_item: dict[str, Any],
    ) -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = []
        standard_metrics, breakdown_specs = self._metric_specs_for_media_item(media_item)
        for metric_name in standard_metrics:
            payload = self._safe_json_get(
                f"{INSTAGRAM_LOGIN_INSIGHTS_BASE_URL}/{media_id}/insights",
                {
                    "metric": metric_name,
                    "access_token": connection.instagram_access_token,
                },
            )
            metric_rows = payload.get("data") if isinstance(payload, dict) else []
            if not metric_rows:
                if INSTAGRAM_DEBUG_LOGGING:
                    print(
                        f"[InstagramAnalyticsService] Empty dataset for metric={metric_name} media_id={media_id}"
                    )
                continue
            for row in metric_rows:
                parsed = self._parse_insight_row(row)
                if parsed:
                    metrics.append(parsed)

        for breakdown_metric, breakdown in breakdown_specs:
            payload = self._safe_json_get(
                f"{INSTAGRAM_LOGIN_INSIGHTS_BASE_URL}/{media_id}/insights",
                {
                    "metric": breakdown_metric,
                    "breakdown": breakdown,
                    "access_token": connection.instagram_access_token,
                },
            )
            metric_rows = payload.get("data") if isinstance(payload, dict) else []
            if not metric_rows:
                if INSTAGRAM_DEBUG_LOGGING:
                    print(
                        f"[InstagramAnalyticsService] Empty breakdown dataset for metric={breakdown_metric} "
                        f"breakdown={breakdown} media_id={media_id}"
                    )
                continue
            for row in metric_rows:
                parsed = self._parse_insight_row(row)
                if parsed:
                    metrics.append(parsed)
        return metrics

    def _metric_specs_for_media_item(
        self,
        media_item: dict[str, Any],
    ) -> tuple[list[str], list[tuple[str, str]]]:
        media_type = str(media_item.get("media_type") or "").upper()
        media_product_type = str(media_item.get("media_product_type") or "").upper()

        if media_type == "STORY":
            return (MEDIA_INSIGHT_METRICS_BY_PRODUCT_TYPE["STORY"], BREAKDOWN_INSIGHT_REQUESTS["STORY"])

        is_reel = media_product_type == "REELS" or media_type == "REEL"
        if is_reel:
            return (MEDIA_INSIGHT_METRICS_BY_PRODUCT_TYPE["REELS"], BREAKDOWN_INSIGHT_REQUESTS["REELS"])

        return (MEDIA_INSIGHT_METRICS_BY_PRODUCT_TYPE["FEED"], BREAKDOWN_INSIGHT_REQUESTS["FEED"])

    def fetch_comments(
        self,
        connection: InstagramConnection,
        media: list[dict[str, Any]],
    ) -> dict[str, Any]:
        samples: list[dict[str, Any]] = []
        for post in media[:8]:
            post_id = post.get("id")
            if not post_id:
                continue
            comments_payload = self._safe_json_get(
                f"{GRAPH_BASE_URL}/{post_id}/comments",
                {
                    "fields": "text,username,timestamp",
                    "limit": 5,
                    "access_token": connection.instagram_access_token,
                },
            )
            for item in comments_payload.get("data", []) if isinstance(comments_payload, dict) else []:
                samples.append(
                    {
                        "username": item.get("username", "unknown"),
                        "text": item.get("text", ""),
                        "timestamp": item.get("timestamp"),
                        "post_id": post_id,
                    }
                )
        return {
            "sample_comments": samples[:15],
            "sentiment_score": None,
            "audience_quality_score": None,
            "nlp_notes": "NLP pipeline placeholder for sentiment and audience quality insights.",
        }

    def normalize_payload(
        self,
        connection: InstagramConnection,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        account = payload.get("account", {})
        performance = payload.get("performance", {})
        engagement = payload.get("engagement", {})
        demographics = payload.get("demographics") or {}
        recent_media = payload.get("recent_media") or []
        missing_metrics: list[str] = []

        account_metrics = normalize_account_metrics(account, performance, missing_metrics)
        normalized_audience = normalize_demographics(demographics, missing_metrics)
        posts = normalize_media_posts(recent_media, payload.get("media_insights") or [], missing_metrics)
        rated_posts = add_post_rates(posts)

        followers = int(
            account_metrics.get("followers_count") or connection.followers_count or 0
        )
        reach = int(account_metrics.get("reach_1d") or 0)
        profile_visits = int(account_metrics.get("profile_views") or 0)
        website_clicks = int(account_metrics.get("website_clicks") or 0)

        total_engagement = (
            int(engagement.get("likes") or 0)
            + int(engagement.get("comments") or 0)
            + int(engagement.get("saved") or 0)
            + int(engagement.get("shares") or 0)
        )

        engagement_rate = round((total_engagement / followers) * 100, 2) if followers > 0 else 0
        reach_ratio = round((reach / followers), 2) if followers > 0 else 0

        media_insights = payload.get("media_insights") or []
        content_performance = self._build_content_performance_rows(media_insights)
        if not content_performance and isinstance(payload.get("content_performance"), list):
            content_performance = [
                row for row in payload.get("content_performance", []) if isinstance(row, dict)
            ]
        content_performance = self._attach_media_details(
            content_performance,
            media_insights=media_insights,
            recent_media=recent_media,
        )
        creator_metrics = calculate_creator_metrics(
            rated_posts,
            followers_count=followers,
            audience=normalized_audience,
            target_filters=None,
        )
        recommendation_labels = build_labels(
            creator_metrics,
            normalized_audience,
            followers,
            missing_metrics,
        )
        insight_cards = build_insight_cards(
            creator_metrics,
            normalized_audience,
            account_metrics,
            missing_metrics,
        )
        data_quality = build_data_quality(
            {
                "username": account.get("username") or connection.instagram_username,
                "followers_count": followers,
            },
            normalized_audience,
            rated_posts,
            missing_metrics,
        )
        dashboard_payload = build_dashboard_payload(
            {
                "username": account.get("username") or connection.instagram_username,
                "biography": account.get("biography") or "",
                "followers_count": followers,
                "follows_count": int(account.get("follows_count") or 0),
                "media_count": int(account.get("media_count") or connection.media_count or 0),
                "account_type": account.get("account_type") or "Creator",
            },
            account_metrics,
            normalized_audience,
            rated_posts,
            creator_metrics,
            recommendation_labels,
            insight_cards,
            data_quality,
        )

        return {
            "account": {
                "username": account.get("username") or connection.instagram_username,
                "bio": account.get("biography") or "",
                "followers_count": followers,
                "following_count": int(account.get("follows_count") or 0),
                "media_count": int(account.get("media_count") or connection.media_count or 0),
                "account_type": account.get("account_type") or "Creator",
            },
            "demographics": payload.get("demographics") or {},
            "performance": {
                "reach": reach,
                "reach_ratio": reach_ratio,
                "profile_visits": profile_visits,
                "website_clicks": website_clicks,
                "insights": performance,
            },
            "engagement": {
                "likes": int(engagement.get("likes") or 0),
                "comments": int(engagement.get("comments") or 0),
                "saves": int(engagement.get("saved") or 0),
                "shares": int(engagement.get("shares") or 0),
                "views": int(engagement.get("views") or 0),
                "total_engagement": total_engagement,
                "engagement_rate": engagement_rate,
            },
            "story": payload.get("story") or {},
            "media_insights": media_insights,
            "content_performance": content_performance,
            "normalized_posts": rated_posts,
            "insight_cards": insight_cards,
            "recommendation_labels": recommendation_labels,
            "creator_value_score": creator_metrics.get("creator_value_score", 0),
            "summary_metrics": creator_metrics,
            "data_quality": data_quality,
            "dashboard": dashboard_payload,
            "comments": payload.get("comments") or {},
            "failed_requests": payload.get("failed_requests") or [],
            "synced_at": payload.get("synced_at"),
        }

    @staticmethod
    def _build_content_performance_rows(media_insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in media_insights:
            if not isinstance(item, dict):
                continue
            metric_values: dict[str, int] = {}
            for metric in item.get("metrics", []):
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("name") or "")
                if not name:
                    continue
                metric_values[name] = int(metric.get("value") or 0)

            likes = metric_values.get("likes", 0)
            comments = metric_values.get("comments", 0)
            saves = metric_values.get("saved", 0)
            shares = metric_values.get("shares", 0)
            views = metric_values.get("views", 0)
            engagement_total = likes + comments + saves + shares
            rows.append(
                {
                    "media_id": str(item.get("media_id") or "-"),
                    "media_type": str(item.get("media_type") or "UNKNOWN"),
                    "media_product_type": str(item.get("media_product_type") or "UNKNOWN"),
                    "timestamp": item.get("timestamp"),
                    "thumbnail_url": item.get("thumbnail_url") or item.get("media_url") or "",
                    "permalink": item.get("permalink") or "",
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "saves": saves,
                    "shares": shares,
                    "engagement_total": engagement_total,
                    "engagement_to_view_rate": round((engagement_total / views) * 100, 2) if views else 0,
                    "performance_score": (views * 2) + (engagement_total * 3),
                }
            )

        rows.sort(key=lambda row: (row["performance_score"], row["views"], row["engagement_total"]), reverse=True)
        return rows

    @staticmethod
    def _attach_media_details(
        rows: list[dict[str, Any]],
        media_insights: list[dict[str, Any]],
        recent_media: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        metadata_by_media_id: dict[str, dict[str, Any]] = {}
        for source in [recent_media, media_insights]:
            for item in source:
                if not isinstance(item, dict):
                    continue
                media_id = str(item.get("media_id") or item.get("id") or "").strip()
                if not media_id:
                    continue
                metadata_by_media_id[media_id] = {
                    "timestamp": item.get("timestamp"),
                    "thumbnail_url": item.get("thumbnail_url") or item.get("media_url") or "",
                    "permalink": item.get("permalink") or "",
                }

        enriched_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            media_id = str(row.get("media_id") or "").strip()
            metadata = metadata_by_media_id.get(media_id, {})
            enriched = dict(row)
            enriched["timestamp"] = row.get("timestamp") or metadata.get("timestamp")
            enriched["thumbnail_url"] = row.get("thumbnail_url") or metadata.get("thumbnail_url") or ""
            enriched["permalink"] = row.get("permalink") or metadata.get("permalink") or ""
            enriched_rows.append(enriched)
        return enriched_rows

    def _resolve_ig_user_id(self, connection: InstagramConnection) -> str:
        candidate_ids = [str(connection.instagram_user_id or "").strip()]
        try:
            me = get_instagram_user(connection.instagram_access_token, connection.instagram_user_id)
            for field in ["user_id", "id"]:
                value = me.get(field)
                if value:
                    candidate_ids.append(str(value))
            username = me.get("username")
            if username and not connection.instagram_username:
                connection.instagram_username = username
                connection.save(update_fields=["instagram_username"])
        except Exception:
            pass

        for candidate in candidate_ids:
            if candidate and candidate.isdigit():
                return candidate
        return str(connection.instagram_user_id)

    def _get_with_fallback(
        self,
        urls: list[str],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        for url in urls:
            payload = self._safe_json_get(url, params)
            if payload:
                return payload
        return {}

    def _safe_json_get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        safe_params = dict(params)
        if "access_token" in safe_params:
            safe_params["access_token"] = "***redacted***"

        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            try:
                data = response.json()
            except ValueError:
                self.failed_requests.append(
                    {
                        "url": url,
                        "params": safe_params,
                        "status_code": response.status_code,
                        "error": "non_json_response",
                    }
                )
                return {}
            if INSTAGRAM_DEBUG_LOGGING:
                print(
                    "[InstagramAnalyticsService] Graph API response:",
                    json.dumps(
                        {
                            "url": url,
                            "params": safe_params,
                            "status_code": response.status_code,
                            "json": data if isinstance(data, dict) else str(data),
                        },
                        default=str,
                    ),
                )
            if response.status_code >= 400 or (isinstance(data, dict) and isinstance(data.get("error"), dict)):
                error = data.get("error") if isinstance(data, dict) else None
                if isinstance(error, dict) and error.get("code") == 10 and "/insights" in url:
                    if INSTAGRAM_DEBUG_LOGGING:
                        print(
                            "[InstagramAnalyticsService] Story insights unavailable for this media "
                            f"(code 10): url={url}"
                        )
                    return {}
                self.failed_requests.append(
                    {
                        "url": url,
                        "params": safe_params,
                        "status_code": response.status_code,
                        "error": error,
                    }
                )
                return {}
            if isinstance(data, dict):
                return data
            self.failed_requests.append(
                {
                    "url": url,
                    "params": safe_params,
                    "status_code": response.status_code,
                    "error": "non_dict_json",
                }
            )
            return {}
        except Exception as exc:
            if INSTAGRAM_DEBUG_LOGGING:
                print(
                    "[InstagramAnalyticsService] Graph API request failed:",
                    json.dumps(
                        {
                            "url": url,
                            "params": safe_params,
                            "error": str(exc),
                        },
                        default=str,
                    ),
                )
            self.failed_requests.append(
                {
                    "url": url,
                    "params": safe_params,
                    "status_code": None,
                    "error": str(exc),
                }
            )
            return {}

    @staticmethod
    def _extract_metric_value(payload: dict[str, Any]) -> int:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            return 0

        row = data[0]
        if not isinstance(row, dict):
            return 0

        values = row.get("values")
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, dict):
                raw_value = first.get("value")
                if isinstance(raw_value, dict):
                    return int(
                        sum(v for v in raw_value.values() if isinstance(v, (int, float)))
                    )
                if isinstance(raw_value, (int, float)):
                    return int(raw_value)

        total_value = row.get("total_value")
        if isinstance(total_value, dict):
            inner_value = total_value.get("value")
            if isinstance(inner_value, dict):
                return int(
                    sum(v for v in inner_value.values() if isinstance(v, (int, float)))
                )
            if isinstance(inner_value, (int, float)):
                return int(inner_value)

        if isinstance(total_value, (int, float)):
            return int(total_value)
        return 0

    @staticmethod
    def _extract_breakdown_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return rows

        for metric_row in data:
            total_value = metric_row.get("total_value") if isinstance(metric_row, dict) else None
            if isinstance(total_value, dict):
                breakdowns = total_value.get("breakdowns") or []
            else:
                breakdowns = metric_row.get("breakdowns") if isinstance(metric_row, dict) else []

            for breakdown in breakdowns or []:
                for result in breakdown.get("results", []) if isinstance(breakdown, dict) else []:
                    labels = result.get("dimension_values") or []
                    label = ", ".join(str(part) for part in labels if part)
                    value = result.get("value")
                    if label and isinstance(value, (int, float)):
                        rows.append({"label": label, "value": int(value)})

        rows.sort(key=lambda row: row["value"], reverse=True)
        return rows[:10]

    @staticmethod
    def _parse_insight_row(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        period = row.get("period") or "lifetime"
        name = row.get("name")
        if not name:
            return None
        parsed = {
            "name": name,
            "value": InstagramAnalyticsService._extract_metric_value({"data": [row]}),
            "period": period,
            "fetched_at": timezone.now().isoformat(),
        }
        breakdown_rows = InstagramAnalyticsService._extract_breakdown_rows({"data": [row]})
        if breakdown_rows:
            parsed["breakdowns"] = breakdown_rows
        return parsed

    @staticmethod
    def empty_metrics() -> dict[str, Any]:
        return {
            "account": {},
            "performance": {},
            "demographics": {
                "audience_gender_age": [],
                "audience_country": [],
                "audience_city": [],
            },
            "engagement": {},
            "story": {},
            "recent_media": [],
            "media_insights": [],
            "content_performance": [],
            "comments": {
                "sample_comments": [],
                "sentiment_score": None,
                "audience_quality_score": None,
                "nlp_notes": "NLP pipeline placeholder for sentiment and audience quality insights.",
            },
            "failed_requests": [],
            "synced_at": None,
        }


class SocialDashboardService:
    def __init__(self, user):
        self.user = user
        self.registry = {
            SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM: InstagramAnalyticsService(user),
        }

    def build_dashboard(self, refresh_platform: str | None = None) -> dict[str, Any]:
        platforms: list[PlatformDashboardData] = []
        for slug, service in self.registry.items():
            should_refresh = refresh_platform == slug
            platforms.append(service.build_platform_data(refresh=should_refresh))

        placeholders = [
            PlatformDashboardData(
                slug="tiktok",
                name="TikTok",
                connected=False,
                can_connect=False,
                connect_url="#",
                refreshed=False,
                last_synced_at=None,
                metrics={},
            ),
            PlatformDashboardData(
                slug="youtube",
                name="YouTube",
                connected=False,
                can_connect=False,
                connect_url="#",
                refreshed=False,
                last_synced_at=None,
                metrics={},
            ),
        ]

        overall = self._build_overall_summary(platforms)
        return {
            "overall": overall,
            "platforms": platforms + placeholders,
        }

    def _build_overall_summary(self, platforms: list[PlatformDashboardData]) -> dict[str, Any]:
        connected = [platform for platform in platforms if platform.connected]
        total_followers = 0
        total_reach = 0
        total_engagement = 0
        engagement_rates = []
        latest_sync = None
        top_countries: dict[str, int] = {}
        top_cities: dict[str, int] = {}
        top_demographics: dict[str, int] = {}

        for platform in connected:
            metrics = platform.metrics
            account = metrics.get("account", {})
            performance = metrics.get("performance", {})
            engagement = metrics.get("engagement", {})
            demographics = metrics.get("demographics", {})

            total_followers += int(account.get("followers_count") or 0)
            total_reach += int(performance.get("reach") or 0)
            total_engagement += int(engagement.get("total_engagement") or 0)
            if isinstance(engagement.get("engagement_rate"), (int, float)):
                engagement_rates.append(float(engagement["engagement_rate"]))

            if platform.last_synced_at and (
                not latest_sync or platform.last_synced_at > latest_sync
            ):
                latest_sync = platform.last_synced_at

            self._accumulate_rows(
                top_demographics,
                demographics.get("audience_gender_age") or [],
            )
            self._accumulate_rows(top_countries, demographics.get("audience_country") or [])
            self._accumulate_rows(top_cities, demographics.get("audience_city") or [])

        return {
            "total_followers": total_followers,
            "total_reach": total_reach,
            "total_engagement": total_engagement,
            "average_engagement_rate": round(sum(engagement_rates) / len(engagement_rates), 2)
            if engagement_rates
            else 0,
            "top_audience_demographics": self._top_rows(top_demographics),
            "top_countries": self._top_rows(top_countries),
            "top_cities": self._top_rows(top_cities),
            "connected_platforms_count": len(connected),
            "last_updated": latest_sync,
        }

    @staticmethod
    def _accumulate_rows(bucket: dict[str, int], rows: list[dict[str, Any]]) -> None:
        for row in rows:
            label = row.get("label")
            value = row.get("value")
            if label and isinstance(value, (int, float)):
                bucket[label] = bucket.get(label, 0) + int(value)

    @staticmethod
    def _top_rows(bucket: dict[str, int]) -> list[dict[str, Any]]:
        return [
            {"label": label, "value": value}
            for label, value in sorted(
                bucket.items(), key=lambda item: item[1], reverse=True
            )[:5]
        ]
