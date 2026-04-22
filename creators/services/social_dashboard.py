from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from django.utils import timezone

from creators.models import SocialAnalyticsSnapshot
from instagram_connect.models import InstagramConnection


REQUEST_TIMEOUT_SECONDS = 15
GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"


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
        snapshot: SocialAnalyticsSnapshot,
    ) -> dict[str, Any]:
        now = timezone.now()
        payload = self.empty_metrics()
        payload["account"] = self.fetch_account(connection)
        payload["performance"] = self.fetch_metric_map(
            connection,
            ["impressions", "reach", "profile_views", "website_clicks"],
        )
        payload["demographics"] = {
            "audience_gender_age": self.fetch_demographic(connection, "follower_demographics", "dimension_values"),
            "audience_country": self.fetch_demographic(connection, "follower_demographics", "country"),
            "audience_city": self.fetch_demographic(connection, "follower_demographics", "city"),
            "audience_locale": self.fetch_demographic(connection, "follower_demographics", "locale"),
        }
        payload["engagement"] = self.fetch_metric_map(
            connection,
            ["likes", "comments", "saved", "shares", "video_views"],
        )
        payload["story"] = self.fetch_story_metrics(connection)
        payload["comments"] = self.fetch_comments(connection)
        payload["synced_at"] = now.isoformat()

        snapshot.payload = payload
        snapshot.save(update_fields=["payload", "synced_at"])

        connection.last_synced_at = now
        connection.save(update_fields=["last_synced_at"])
        return payload

    def fetch_account(self, connection: InstagramConnection) -> dict[str, Any]:
        url = f"{GRAPH_BASE_URL}/{connection.instagram_user_id}"
        params = {
            "fields": "username,biography,followers_count,follows_count,media_count,account_type",
            "access_token": connection.access_token,
        }
        return self._safe_json_get(url, params=params)

    def fetch_metric_map(self, connection: InstagramConnection, metrics: list[str]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for metric in metrics:
            values[metric] = self.fetch_single_metric(connection, metric)
        return values

    def fetch_single_metric(self, connection: InstagramConnection, metric: str) -> int:
        url = f"{GRAPH_BASE_URL}/{connection.instagram_user_id}/insights"
        params = {
            "metric": metric,
            "period": "day",
            "metric_type": "total_value",
            "access_token": connection.access_token,
        }
        payload = self._safe_json_get(url, params=params)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            return 0
        values = data[0].get("values")
        if isinstance(values, list) and values:
            value = values[0].get("value")
            if isinstance(value, dict):
                return int(sum(v for v in value.values() if isinstance(v, (int, float))))
            if isinstance(value, (int, float)):
                return int(value)
        total_value = data[0].get("total_value")
        if isinstance(total_value, dict):
            raw_value = total_value.get("value")
            if isinstance(raw_value, (int, float)):
                return int(raw_value)
        return 0

    def fetch_demographic(self, connection: InstagramConnection, metric: str, breakdown: str) -> list[dict[str, Any]]:
        url = f"{GRAPH_BASE_URL}/{connection.instagram_user_id}/insights"
        params = {
            "metric": metric,
            "period": "lifetime",
            "breakdown": breakdown,
            "metric_type": "total_value",
            "access_token": connection.access_token,
        }
        payload = self._safe_json_get(url, params=params)
        result: list[dict[str, Any]] = []
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return result
        for item in data:
            total_value = item.get("total_value") if isinstance(item, dict) else None
            if not isinstance(total_value, dict):
                continue
            for breakdown_item in total_value.get("breakdowns", []) or []:
                for row in breakdown_item.get("results", []) or []:
                    dimensions = row.get("dimension_values") or []
                    key = ", ".join(str(part) for part in dimensions if part)
                    value = row.get("value")
                    if key and isinstance(value, (int, float)):
                        result.append({"label": key, "value": int(value)})
        return result[:10]

    def fetch_story_metrics(self, connection: InstagramConnection) -> dict[str, int]:
        metrics = {"exits": 0, "taps_forward": 0, "taps_back": 0, "replies": 0}
        media_url = f"{GRAPH_BASE_URL}/{connection.instagram_user_id}/media"
        media_params = {
            "fields": "id,media_type",
            "limit": 10,
            "access_token": connection.access_token,
        }
        media_payload = self._safe_json_get(media_url, params=media_params)
        data = media_payload.get("data") if isinstance(media_payload, dict) else None
        if not isinstance(data, list):
            return metrics

        story_ids = [item.get("id") for item in data if isinstance(item, dict) and item.get("media_type") == "STORY"]
        for story_id in story_ids[:5]:
            insights_url = f"{GRAPH_BASE_URL}/{story_id}/insights"
            insights_params = {
                "metric": "exits,replies,taps_forward,taps_back",
                "access_token": connection.access_token,
            }
            insight_payload = self._safe_json_get(insights_url, params=insights_params)
            for row in insight_payload.get("data", []) if isinstance(insight_payload, dict) else []:
                name = row.get("name")
                values = row.get("values")
                if name in metrics and isinstance(values, list) and values:
                    value = values[0].get("value")
                    if isinstance(value, (int, float)):
                        metrics[name] += int(value)
        return metrics

    def fetch_comments(self, connection: InstagramConnection) -> dict[str, Any]:
        media_url = f"{GRAPH_BASE_URL}/{connection.instagram_user_id}/media"
        media_params = {
            "fields": "id,caption,like_count,comments_count",
            "limit": 5,
            "access_token": connection.access_token,
        }
        media_payload = self._safe_json_get(media_url, params=media_params)
        samples: list[dict[str, Any]] = []
        for post in media_payload.get("data", []) if isinstance(media_payload, dict) else []:
            post_id = post.get("id")
            if not post_id:
                continue
            comments_url = f"{GRAPH_BASE_URL}/{post_id}/comments"
            comments_payload = self._safe_json_get(
                comments_url,
                params={"fields": "text,username,timestamp", "limit": 3, "access_token": connection.access_token},
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
            "sample_comments": samples[:10],
            "sentiment_score": None,
            "audience_quality_score": None,
            "nlp_notes": "NLP pipeline placeholder for sentiment and audience quality insights.",
        }

    def normalize_payload(self, connection: InstagramConnection, payload: dict[str, Any]) -> dict[str, Any]:
        account = payload.get("account", {})
        performance = payload.get("performance", {})
        engagement = payload.get("engagement", {})
        followers = int(account.get("followers_count") or connection.followers_count or 0)
        reach = int(performance.get("reach") or 0)
        impressions = int(performance.get("impressions") or 0)
        profile_visits = int(performance.get("profile_views") or 0)
        website_clicks = int(performance.get("website_clicks") or 0)

        total_engagement = int(engagement.get("likes") or 0) + int(engagement.get("comments") or 0) + int(engagement.get("saved") or 0) + int(engagement.get("shares") or 0)
        engagement_rate = round((total_engagement / followers) * 100, 2) if followers > 0 else 0
        reach_ratio = round((reach / followers), 2) if followers > 0 else 0

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
                "impressions": impressions,
                "reach": reach,
                "reach_ratio": reach_ratio,
                "profile_visits": profile_visits,
                "website_clicks": website_clicks,
            },
            "engagement": {
                "likes": int(engagement.get("likes") or 0),
                "comments": int(engagement.get("comments") or 0),
                "saves": int(engagement.get("saved") or 0),
                "shares": int(engagement.get("shares") or 0),
                "video_views": int(engagement.get("video_views") or 0),
                "total_engagement": total_engagement,
                "engagement_rate": engagement_rate,
            },
            "story": payload.get("story") or {},
            "comments": payload.get("comments") or {},
            "synced_at": payload.get("synced_at"),
        }

    def _safe_json_get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            data = response.json()
            if response.status_code >= 400:
                return {}
            if isinstance(data, dict):
                return data
            return {}
        except Exception:
            return {}

    @staticmethod
    def empty_metrics() -> dict[str, Any]:
        return {
            "account": {},
            "performance": {},
            "demographics": {},
            "engagement": {},
            "story": {},
            "comments": {
                "sample_comments": [],
                "sentiment_score": None,
                "audience_quality_score": None,
                "nlp_notes": "NLP pipeline placeholder for sentiment and audience quality insights.",
            },
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
        total_impressions = 0
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
            total_impressions += int(performance.get("impressions") or 0)
            total_engagement += int(engagement.get("total_engagement") or 0)
            if isinstance(engagement.get("engagement_rate"), (int, float)):
                engagement_rates.append(float(engagement["engagement_rate"]))

            if platform.last_synced_at and (not latest_sync or platform.last_synced_at > latest_sync):
                latest_sync = platform.last_synced_at

            self._accumulate_rows(top_demographics, demographics.get("audience_gender_age") or [])
            self._accumulate_rows(top_countries, demographics.get("audience_country") or [])
            self._accumulate_rows(top_cities, demographics.get("audience_city") or [])

        return {
            "total_followers": total_followers,
            "total_reach": total_reach,
            "total_impressions": total_impressions,
            "total_engagement": total_engagement,
            "average_engagement_rate": round(sum(engagement_rates) / len(engagement_rates), 2) if engagement_rates else 0,
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
            for label, value in sorted(bucket.items(), key=lambda item: item[1], reverse=True)[:5]
        ]
