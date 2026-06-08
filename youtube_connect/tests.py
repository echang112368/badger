from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from creators.models import SocialAnalyticsSnapshot
from youtube_connect.models import YouTubeConnection
from youtube_connect.services import build_oauth_url


def _channel():
    return {
        "id": "UC123",
        "snippet": {
            "title": "Creator Channel",
            "description": "Videos",
            "customUrl": "@creator",
            "thumbnails": {"default": {"url": "https://example.com/thumb.jpg"}},
            "country": "US",
        },
        "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
        "statistics": {
            "viewCount": "1000",
            "subscriberCount": "250",
            "hiddenSubscriberCount": False,
            "videoCount": "12",
        },
        "brandingSettings": {"channel": {"country": "US"}},
    }


class YouTubeOAuthUrlTests(TestCase):
    @override_settings(
        YOUTUBE_CLIENT_ID="youtube-client",
        YOUTUBE_REDIRECT_URI="https://example.com/youtube/callback",
        YOUTUBE_OAUTH_SCOPES=(
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ),
    )
    def test_build_oauth_url_includes_google_web_server_params(self):
        url = build_oauth_url("state_abc")
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/v2/auth")
        self.assertEqual(params["client_id"], ["youtube-client"])
        self.assertEqual(params["redirect_uri"], ["https://example.com/youtube/callback"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["state"], ["state_abc"])
        self.assertEqual(
            params["scope"],
            ["https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"],
        )
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["include_granted_scopes"], ["true"])


class YouTubeCallbackFlowTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="yt_callback_user",
            email="yt_callback@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["youtube_oauth_state"] = "state_abc"
        session.save()

    def test_callback_rejects_missing_code(self):
        response = self.client.get(reverse("youtube_callback"), {"state": "state_abc"})
        self.assertRedirects(response, f"{reverse('creator_settings')}?youtube_oauth=error")

    def test_callback_rejects_state_mismatch(self):
        response = self.client.get(reverse("youtube_callback"), {"code": "code_123", "state": "wrong"})
        self.assertRedirects(response, f"{reverse('creator_settings')}?youtube_oauth=error")

    @patch("youtube_connect.views.get_authenticated_channel")
    @patch("youtube_connect.views.exchange_code_for_tokens")
    def test_callback_exchanges_code_and_persists_youtube_connection(self, mock_exchange, mock_channel):
        mock_exchange.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }
        mock_channel.return_value = _channel()

        response = self.client.get(reverse("youtube_callback"), {"code": "code_123", "state": "state_abc"})

        self.assertRedirects(response, f"{reverse('creator_settings')}?youtube_oauth=success")
        connection = YouTubeConnection.objects.get(user=self.user)
        self.assertEqual(connection.youtube_channel_id, "UC123")
        self.assertEqual(connection.youtube_channel_title, "Creator Channel")
        self.assertEqual(connection.youtube_access_token, "access-token")
        self.assertEqual(connection.youtube_refresh_token, "refresh-token")
        self.assertEqual(connection.subscribers_count, 250)


class YouTubeStatusDisconnectSyncTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="yt_status_user",
            email="yt_status@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(self.user)

    def test_status_returns_connected_false_when_no_connection(self):
        response = self.client.get(reverse("youtube_status"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"connected": False})

    def test_status_returns_channel_counts_when_connected(self):
        YouTubeConnection.objects.create(
            user=self.user,
            youtube_channel_id="UC123",
            youtube_channel_title="Creator Channel",
            youtube_channel_handle="@creator",
            subscribers_count=250,
            video_count=12,
            view_count=1000,
            youtube_access_token="access-token",
            token_expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse("youtube_status"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["connected"])
        self.assertEqual(payload["youtube_channel_id"], "UC123")
        self.assertEqual(payload["subscribers_count"], 250)
        self.assertEqual(payload["video_count"], 12)
        self.assertEqual(payload["view_count"], 1000)

    def test_disconnect_deletes_connection_and_youtube_snapshot(self):
        YouTubeConnection.objects.create(user=self.user, youtube_channel_id="UC123", youtube_access_token="token")
        SocialAnalyticsSnapshot.objects.create(
            user=self.user,
            platform=SocialAnalyticsSnapshot.PLATFORM_YOUTUBE,
            payload={"account": {"subscriber_count": 10}},
        )

        response = self.client.post(reverse("youtube_disconnect"))

        self.assertRedirects(response, f"{reverse('creator_settings')}?youtube_disconnect=success")
        self.assertFalse(YouTubeConnection.objects.filter(user=self.user).exists())
        self.assertFalse(
            SocialAnalyticsSnapshot.objects.filter(user=self.user, platform=SocialAnalyticsSnapshot.PLATFORM_YOUTUBE).exists()
        )

    @patch("creators.services.social_dashboard.query_youtube_analytics")
    @patch("creators.services.social_dashboard.fetch_videos")
    @patch("creators.services.social_dashboard.fetch_uploads_playlist_items")
    @patch("creators.services.social_dashboard.get_authenticated_channel")
    @patch("youtube_connect.views.get_authenticated_channel")
    @patch("youtube_connect.views.refresh_access_token")
    def test_sync_refreshes_token_fetches_metrics_and_updates_snapshot(
        self,
        mock_refresh,
        mock_view_channel,
        mock_service_channel,
        mock_playlist,
        mock_videos,
        mock_analytics,
    ):
        connection = YouTubeConnection.objects.create(
            user=self.user,
            youtube_channel_id="UC123",
            youtube_channel_title="Old",
            youtube_access_token="old-token",
            youtube_refresh_token="refresh-token",
            token_expires_at=timezone.now() - timedelta(minutes=1),
        )
        mock_refresh.return_value = {"access_token": "new-token", "expires_in": 3600}
        mock_view_channel.return_value = _channel()
        mock_service_channel.return_value = _channel()
        mock_playlist.return_value = {"items": [{"contentDetails": {"videoId": "vid1"}}]}
        mock_videos.return_value = {
            "items": [
                {
                    "id": "vid1",
                    "snippet": {"title": "Video 1", "publishedAt": "2026-01-01T00:00:00Z", "thumbnails": {"default": {"url": "thumb"}}},
                    "contentDetails": {"duration": "PT1M"},
                    "statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "2"},
                    "status": {"privacyStatus": "public"},
                }
            ]
        }
        mock_analytics.return_value = {
            "columnHeaders": [{"name": "views"}, {"name": "likes"}, {"name": "comments"}, {"name": "shares"}],
            "rows": [[100, 5, 2, 1]],
        }

        response = self.client.get(reverse("youtube_sync"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        connection.refresh_from_db()
        self.assertEqual(connection.youtube_access_token, "new-token")
        self.assertEqual(connection.youtube_channel_title, "Creator Channel")
        self.assertEqual(connection.subscribers_count, 250)
        snapshot = SocialAnalyticsSnapshot.objects.get(user=self.user, platform=SocialAnalyticsSnapshot.PLATFORM_YOUTUBE)
        self.assertEqual(snapshot.payload["account"]["channel_id"], "UC123")
        self.assertEqual(snapshot.payload["recent_media"][0]["video_id"], "vid1")
