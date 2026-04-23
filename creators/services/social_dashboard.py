from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from django.utils import timezone

from creators.models import SocialAnalyticsSnapshot
from instagram_connect.models import InstagramConnection
from instagram_connect.services import get_graph_api_base, get_instagram_user


REQUEST_TIMEOUT_SECONDS = 15
GRAPH_BASE_URL = get_graph_api_base()
GRAPH_BASIC_URL = get_graph_api_base()


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
        payload["engagement"] = self.fetch_engagement_metrics(connection, recent_media)
        payload["story"] = self.fetch_story_metrics(connection, recent_media)
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
            [
                f"{GRAPH_BASE_URL}/{ig_user_id}",
                f"{GRAPH_BASIC_URL}/me",
            ],
            {
                "fields": fields,
                "access_token": connection.access_token,
            },
        )

    def fetch_account_performance(
        self, connection: InstagramConnection, ig_user_id: str
    ) -> dict[str, int]:
        metrics = {
            "impressions": 0,
            "reach": 0,
            "profile_views": 0,
            "website_clicks": 0,
        }
        # Try account-level insights first.
        for metric_name in ["impressions", "reach", "profile_views", "website_clicks"]:
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
    ) -> int:
        payload = self._safe_json_get(
            f"{GRAPH_BASE_URL}/{ig_user_id}/insights",
            {
                "metric": metric,
                "period": "day",
                "access_token": connection.access_token,
            },
        )
        return self._extract_metric_value(payload)

    def fetch_demographics(
        self,
        connection: InstagramConnection,
        ig_user_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        gender_age = self.fetch_demographic_breakdown(
            connection,
            ig_user_id,
            breakdown="age,gender",
        )
        countries = self.fetch_demographic_breakdown(
            connection,
            ig_user_id,
            breakdown="country",
        )
        cities = self.fetch_demographic_breakdown(
            connection,
            ig_user_id,
            breakdown="city",
        )
        locales = self.fetch_demographic_breakdown(
            connection,
            ig_user_id,
            breakdown="locale",
        )

        return {
            "audience_gender_age": gender_age,
            "audience_country": countries,
            "audience_city": cities,
            "audience_locale": locales,
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
                "access_token": connection.access_token,
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
                "fields": "id,media_type,like_count,comments_count,timestamp",
                "limit": limit,
                "access_token": connection.access_token,
            },
        )
        data = media_payload.get("data") if isinstance(media_payload, dict) else []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def fetch_engagement_metrics(
        self,
        connection: InstagramConnection,
        media: list[dict[str, Any]],
    ) -> dict[str, int]:
        totals = {
            "likes": 0,
            "comments": 0,
            "saved": 0,
            "shares": 0,
            "video_views": 0,
        }

        for item in media:
            media_id = item.get("id")
            if not media_id:
                continue

            totals["likes"] += int(item.get("like_count") or 0)
            totals["comments"] += int(item.get("comments_count") or 0)

            metric_candidates = ["saved", "shares", "views", "plays"]
            insight_payload = self._safe_json_get(
                f"{GRAPH_BASE_URL}/{media_id}/insights",
                {
                    "metric": ",".join(metric_candidates),
                    "access_token": connection.access_token,
                },
            )
            metric_rows = insight_payload.get("data") if isinstance(insight_payload, dict) else []
            for row in metric_rows:
                if not isinstance(row, dict):
                    continue
                name = row.get("name")
                value = self._extract_metric_value({"data": [row]})
                if name == "saved":
                    totals["saved"] += value
                elif name == "shares":
                    totals["shares"] += value
                elif name in {"views", "plays"}:
                    totals["video_views"] += value

        return totals

    def fetch_story_metrics(
        self,
        connection: InstagramConnection,
        media: list[dict[str, Any]],
    ) -> dict[str, int]:
        metrics = {"exits": 0, "taps_forward": 0, "taps_back": 0, "replies": 0}
        story_ids = [item.get("id") for item in media if item.get("media_type") == "STORY"]
        for story_id in story_ids[:10]:
            payload = self._safe_json_get(
                f"{GRAPH_BASE_URL}/{story_id}/insights",
                {
                    "metric": "exits,replies,taps_forward,taps_back",
                    "access_token": connection.access_token,
                },
            )
            rows = payload.get("data") if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                metric_name = row.get("name")
                if metric_name in metrics:
                    metrics[metric_name] += self._extract_metric_value({"data": [row]})
        return metrics

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
                    "access_token": connection.access_token,
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

        followers = int(account.get("followers_count") or connection.followers_count or 0)
        reach = int(performance.get("reach") or 0)
        impressions = int(performance.get("impressions") or 0)
        profile_visits = int(performance.get("profile_views") or 0)
        website_clicks = int(performance.get("website_clicks") or 0)

        total_engagement = (
            int(engagement.get("likes") or 0)
            + int(engagement.get("comments") or 0)
            + int(engagement.get("saved") or 0)
            + int(engagement.get("shares") or 0)
        )

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
            "failed_requests": payload.get("failed_requests") or [],
            "synced_at": payload.get("synced_at"),
        }

    def _resolve_ig_user_id(self, connection: InstagramConnection) -> str:
        candidate_ids = [str(connection.instagram_user_id or "").strip()]
        try:
            me = get_instagram_user(connection.instagram_user_id, connection.access_token)
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
            data = response.json()
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
            if response.status_code >= 400:
                self.failed_requests.append(
                    {
                        "url": url,
                        "params": safe_params,
                        "status_code": response.status_code,
                        "error": data.get("error") if isinstance(data, dict) else None,
                    }
                )
                return {}
            if isinstance(data, dict):
                if data.get("data") in (None, []) and any(
                    key in safe_params for key in ("metric", "fields", "breakdown")
                ):
                    self.failed_requests.append(
                        {
                            "url": url,
                            "params": safe_params,
                            "status_code": response.status_code,
                            "error": "empty_data",
                        }
                    )
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
            "total_impressions": total_impressions,
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
