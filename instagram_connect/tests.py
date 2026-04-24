from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from creators.models import SocialAnalyticsSnapshot
from creators.services.social_dashboard import InstagramAnalyticsService
from instagram_connect.models import InstagramConnection
from instagram_connect.services import (
    build_oauth_url,
    exchange_code_for_access_token,
    get_instagram_user,
    resolve_meta_oauth_scopes,
)


class MetaOAuthScopeTests(TestCase):
    @override_settings(
        META_OAUTH_SCOPES=(
            "instagram_business_basic,instagram_business_manage_insights,instagram_business_manage_insights"
        )
    )
    def test_resolve_meta_oauth_scopes_deduplicates_string_value(self):
        self.assertEqual(
            resolve_meta_oauth_scopes(),
            "instagram_business_basic,instagram_business_manage_insights",
        )

    @override_settings(
        META_APP_ID="app_123",
        META_REDIRECT_URI="https://example.com/callback",
        META_OAUTH_SCOPES=("instagram_business_basic", "instagram_business_manage_insights"),
    )
    def test_connect_route_uses_instagram_oauth_dialog(self):
        user = CustomUser.objects.create_user(
            username="oauth_start_user",
            email="oauth_start_user@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("connect_instagram"))

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response.url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.instagram.com")
        self.assertEqual(parsed.path, "/oauth/authorize")
        params = parse_qs(parsed.query)
        self.assertEqual(params["client_id"], ["app_123"])
        self.assertEqual(params["redirect_uri"], ["https://example.com/callback"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(
            params["scope"],
            ["instagram_business_basic,instagram_business_manage_insights"],
        )
        self.assertTrue(params["state"][0])


class MetaServiceHttpTests(SimpleTestCase):
    @override_settings(
        META_APP_ID="app_123",
        META_APP_SECRET="secret_123",
        META_REDIRECT_URI="https://example.com/callback",
    )
    @patch("instagram_connect.services.requests.post")
    def test_exchange_code_uses_instagram_token_endpoint(self, mock_post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access_token": "token_abc", "expires_in": 3600}
        mock_post.return_value = response

        token_data = exchange_code_for_access_token("code_abc")

        mock_post.assert_called_once_with(
            "https://api.instagram.com/oauth/access_token",
            data={
                "client_id": "app_123",
                "client_secret": "secret_123",
                "grant_type": "authorization_code",
                "redirect_uri": "https://example.com/callback",
                "code": "code_abc",
            },
            timeout=15,
        )
        self.assertEqual(token_data["access_token"], "token_abc")

    @override_settings(
        META_APP_ID="app_123",
        META_REDIRECT_URI="https://example.com/callback",
        META_OAUTH_SCOPES=("instagram_business_basic", "instagram_business_manage_insights"),
    )
    def test_build_oauth_url_includes_scope(self):
        url = build_oauth_url("state_abc")
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        self.assertEqual(params["scope"], ["instagram_business_basic,instagram_business_manage_insights"])

    @override_settings(META_API_VERSION="v22.0")
    @patch("instagram_connect.services.requests.get")
    def test_get_instagram_user_uses_graph_instagram(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"id": "178900000000001", "username": "creator"}
        mock_get.return_value = response

        payload = get_instagram_user("user_token", "178900000000001")

        mock_get.assert_called_once_with(
            "https://graph.instagram.com/v22.0/178900000000001",
            params={
                "fields": "id,username,biography,followers_count,follows_count,media_count,account_type",
                "access_token": "user_token",
            },
            timeout=15,
        )
        self.assertEqual(payload["id"], "178900000000001")


class InstagramCallbackFlowTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="ig_callback_user",
            email="ig_callback@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["instagram_oauth_state"] = "state_abc"
        session.save()

    @patch("instagram_connect.views.get_instagram_user")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_persists_instagram_connection(
        self,
        mock_exchange,
        mock_exchange_long,
        mock_get_ig,
    ):
        mock_exchange.return_value = {"access_token": "short_token", "expires_in": 3600}
        mock_exchange_long.return_value = {"access_token": "long_token", "expires_in": 5184000}
        mock_get_ig.return_value = {
            "id": "1789",
            "username": "creator_name",
            "followers_count": 42,
            "media_count": 3,
        }

        response = self.client.get(
            reverse("instagram_callback"),
            {"code": "code_123", "state": "state_abc"},
        )

        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_oauth=success")
        connection = InstagramConnection.objects.get(user=self.user)
        self.assertEqual(connection.instagram_user_id, "1789")
        self.assertEqual(connection.instagram_access_token, "long_token")

    def test_callback_rejects_state_mismatch(self):
        response = self.client.get(
            reverse("instagram_callback"),
            {"code": "code_123", "state": "wrong"},
        )
        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_oauth=error")


class InstagramAnalyticsResilienceTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="ig_sync_user",
            email="ig_sync@example.com",
            password="pass123",
            is_creator=True,
        )
        self.connection = InstagramConnection.objects.create(
            user=self.user,
            instagram_user_id="123456789",
            instagram_access_token="token",
            access_token="token",
        )

    @patch("creators.services.social_dashboard.requests.get")
    def test_unsupported_metric_error_is_collected_not_crash(self, mock_get):
        unsupported = Mock()
        unsupported.status_code = 400
        unsupported.json.return_value = {"error": {"message": "Unsupported metric"}}
        mock_get.return_value = unsupported

        service = InstagramAnalyticsService(self.user)
        value = service.fetch_single_account_metric(self.connection, "123", "likes")

        self.assertIsNone(value)
        self.assertTrue(service.failed_requests)

    @patch("creators.services.social_dashboard.requests.get")
    def test_api_error_returns_empty_media_list(self, mock_get):
        response = Mock()
        response.status_code = 500
        response.json.return_value = {"error": {"message": "Server error"}}
        mock_get.return_value = response

        service = InstagramAnalyticsService(self.user)
        media = service.fetch_recent_media(self.connection, "123")
        self.assertEqual(media, [])

    @patch.object(InstagramAnalyticsService, "fetch_account", return_value={"followers_count": 100, "media_count": 3})
    @patch.object(InstagramAnalyticsService, "fetch_account_performance", return_value={"reach": 30, "profile_views": None, "website_clicks": None})
    @patch.object(InstagramAnalyticsService, "fetch_demographics", return_value={"audience_country": []})
    @patch.object(InstagramAnalyticsService, "fetch_recent_media", return_value=[])
    @patch.object(InstagramAnalyticsService, "fetch_engagement_metrics", return_value={"likes": 1, "comments": 1, "saved": 0, "shares": 0})
    @patch.object(InstagramAnalyticsService, "fetch_story_metrics", return_value={})
    @patch.object(InstagramAnalyticsService, "fetch_comments", return_value={"sample_comments": []})
    def test_dashboard_persists_partial_analytics(self, *_mocks):
        snapshot = SocialAnalyticsSnapshot.objects.create(
            user=self.user,
            platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
            payload={},
        )
        payload = InstagramAnalyticsService(self.user).fetch_and_cache(self.connection, snapshot=snapshot)
        self.assertIn("account", payload)
        self.assertIn("performance", payload)
        snapshot.refresh_from_db()
        self.assertIn("account", snapshot.payload)


class InstagramDisconnectTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="ig_disconnect_user",
            email="ig_disconnect@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(self.user)

    def test_disconnect_removes_connection_and_instagram_snapshot(self):
        InstagramConnection.objects.create(
            user=self.user,
            instagram_user_id="123456789",
            instagram_access_token="token",
            access_token="token",
        )
        SocialAnalyticsSnapshot.objects.create(
            user=self.user,
            platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
            payload={"account": {"followers_count": 10}},
        )

        response = self.client.post(reverse("instagram_disconnect"))

        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_disconnect=success")
        self.assertFalse(InstagramConnection.objects.filter(user=self.user).exists())
        self.assertFalse(
            SocialAnalyticsSnapshot.objects.filter(
                user=self.user,
                platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
            ).exists()
        )
