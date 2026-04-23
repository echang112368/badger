from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from instagram_connect.models import InstagramConnection
from instagram_connect.services import (
    MetaAPIError,
    build_oauth_url,
    exchange_code_for_access_token,
    exchange_for_long_lived_access_token,
    get_instagram_user,
    refresh_long_lived_access_token,
    resolve_meta_oauth_scopes,
    should_refresh_token,
)


class MetaOAuthScopeTests(SimpleTestCase):
    @override_settings(
        META_OAUTH_SCOPES=(
            "instagram_basic,instagram_manage_comments,"
            "instagram_manage_comments"
        )
    )
    def test_resolve_meta_oauth_scopes_deduplicates_string_value(self):
        self.assertEqual(
            resolve_meta_oauth_scopes(),
            "instagram_basic,instagram_manage_comments",
        )

    @override_settings(
        META_OAUTH_SCOPES=[
            "instagram_basic",
            "instagram_manage_insights",
            "instagram_basic",
        ]
    )
    def test_resolve_meta_oauth_scopes_deduplicates_iterables(self):
        self.assertEqual(
            resolve_meta_oauth_scopes(),
            "instagram_basic,instagram_manage_insights",
        )

    @override_settings(META_APP_ID="app_123", META_REDIRECT_URI="https://example.com/callback")
    def test_build_oauth_url_uses_configured_scopes(self):
        with override_settings(
            META_OAUTH_SCOPES=["instagram_basic", "instagram_manage_insights"],
            META_ENABLE_FB_LOGIN=False,
            META_FORCE_REAUTH=True,
        ):
            oauth_url = build_oauth_url("state_abc")

        parsed = urlparse(oauth_url)
        params = parse_qs(parsed.query)

        self.assertEqual(params["client_id"], ["app_123"])
        self.assertEqual(params["redirect_uri"], ["https://example.com/callback"])
        self.assertEqual(
            params["scope"],
            ["instagram_basic,instagram_manage_insights"],
        )
        self.assertEqual(params["state"], ["state_abc"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["enable_fb_login"], ["false"])
        self.assertEqual(params["force_reauth"], ["true"])

    @override_settings(
        META_APP_ID="app_123",
        META_APP_SECRET="secret_123",
        META_REDIRECT_URI="https://example.com/callback",
    )
    @patch("instagram_connect.services.requests.post")
    def test_exchange_code_posts_to_instagram_oauth_endpoint(self, mock_post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "data": [
                {
                    "access_token": "token_abc",
                    "user_id": "1789",
                    "permissions": "instagram_basic",
                }
            ]
        }
        mock_post.return_value = response

        token_data = exchange_code_for_access_token("code_abc")

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["data"],
            {
                "client_id": "app_123",
                "client_secret": "secret_123",
                "grant_type": "authorization_code",
                "redirect_uri": "https://example.com/callback",
                "code": "code_abc",
            },
        )
        self.assertEqual(token_data["access_token"], "token_abc")

    @patch("instagram_connect.services.requests.get")
    def test_get_instagram_user_requests_metrics_fields(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "id": "1789",
            "username": "creator",
            "followers_count": 123,
            "media_count": 45,
        }
        mock_get.return_value = response

        get_instagram_user("token_abc")

        mock_get.assert_called_once_with(
            "https://graph.instagram.com/me",
            params={
                "fields": "id,user_id,username,followers_count,media_count",
                "access_token": "token_abc",
            },
            timeout=15,
        )

    @override_settings(META_APP_SECRET="secret_123")
    @patch("instagram_connect.services.requests.get")
    def test_exchange_for_long_lived_access_token_uses_expected_endpoint(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access_token": "long_token", "expires_in": 5184000}
        mock_get.return_value = response

        token_data = exchange_for_long_lived_access_token("short_token")

        mock_get.assert_called_once_with(
            "https://graph.instagram.com/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": "secret_123",
                "access_token": "short_token",
            },
            timeout=15,
        )
        self.assertEqual(token_data["access_token"], "long_token")

    @patch("instagram_connect.services.requests.get")
    def test_refresh_long_lived_access_token_uses_expected_endpoint(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access_token": "new_token", "expires_in": 5184000}
        mock_get.return_value = response

        token_data = refresh_long_lived_access_token("old_token")

        mock_get.assert_called_once_with(
            "https://graph.instagram.com/refresh_access_token",
            params={
                "grant_type": "ig_refresh_token",
                "access_token": "old_token",
            },
            timeout=15,
        )
        self.assertEqual(token_data["access_token"], "new_token")

    def test_should_refresh_token_when_expiry_missing(self):
        self.assertTrue(should_refresh_token(None))

    def test_should_refresh_token_when_expiry_far_in_future(self):
        self.assertFalse(should_refresh_token(timezone.now() + timedelta(days=7)))


class InstagramCallbackRedirectTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="ig_callback_user",
            email="ig_callback@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["meta_oauth_state"] = "state_abc"
        session.save()

    @patch("instagram_connect.views.get_instagram_user")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_redirects_to_settings_with_success_flag(
        self,
        mock_exchange,
        mock_exchange_long_lived,
        mock_get_user,
    ):
        mock_exchange.return_value = {"access_token": "token_123", "expires_in": 5184000}
        mock_exchange_long_lived.return_value = {
            "access_token": "long_token_123",
            "expires_in": 5184000,
        }
        mock_get_user.return_value = {
            "id": "ig_1",
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
        self.assertEqual(connection.instagram_user_id, "ig_1")
        self.assertEqual(connection.access_token, "token_123")

    @patch("instagram_connect.views.get_instagram_user")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_upgrades_short_lived_tokens_when_expires_in_is_string(
        self,
        mock_exchange,
        mock_exchange_long_lived,
        mock_get_user,
    ):
        mock_exchange.return_value = {"access_token": "token_123", "expires_in": "3600"}
        mock_exchange_long_lived.return_value = {
            "access_token": "long_token_123",
            "expires_in": 5184000,
        }
        mock_get_user.return_value = {
            "id": "ig_1",
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
        self.assertEqual(connection.access_token, "long_token_123")
        mock_exchange_long_lived.assert_called_once_with("token_123")

    def test_callback_redirects_to_settings_with_error_flag_for_oauth_error(self):
        response = self.client.get(
            reverse("instagram_callback"),
            {"error": "access_denied", "state": "state_abc"},
        )

        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_oauth=error")
        self.assertFalse(InstagramConnection.objects.filter(user=self.user).exists())


class InstagramSyncTokenRefreshTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="ig_sync_user",
            email="ig_sync@example.com",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(self.user)
        self.connection = InstagramConnection.objects.create(
            user=self.user,
            instagram_user_id="123456789",
            instagram_username="before_refresh",
            access_token="stale_token",
        )

    @patch("instagram_connect.views.InstagramAnalyticsService.fetch_and_cache")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.refresh_long_lived_access_token")
    @patch("instagram_connect.views.get_instagram_user")
    def test_sync_refreshes_token_and_retries_when_session_key_is_invalid(
        self,
        mock_get_user,
        mock_refresh_token,
        mock_exchange_long_lived,
        mock_fetch_and_cache,
    ):
        mock_get_user.side_effect = [
            MetaAPIError("Session key invalid."),
            {
                "id": "123456789",
                "username": "after_refresh",
                "followers_count": 101,
                "media_count": 12,
            },
        ]
        mock_refresh_token.return_value = {
            "access_token": "fresh_token",
            "expires_in": 5184000,
        }
        mock_exchange_long_lived.return_value = {
            "access_token": "fresh_token",
            "expires_in": 5184000,
        }
        mock_fetch_and_cache.return_value = {
            "account": {"followers_count": 202, "media_count": 22},
            "failed_requests": [
                {
                    "url": "https://graph.instagram.com/123456789/insights",
                    "params": {"metric": "website_clicks"},
                    "status_code": 400,
                    "error": "Unsupported metric",
                }
            ],
        }

        response = self.client.get(reverse("instagram_sync"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json().get("failed_requests", [])), 1)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.access_token, "fresh_token")
        self.assertEqual(self.connection.instagram_username, "after_refresh")
        self.assertEqual(self.connection.followers_count, 202)
        self.assertEqual(self.connection.media_count, 22)

    @patch("instagram_connect.views.refresh_long_lived_access_token")
    @patch("instagram_connect.views.get_instagram_user")
    def test_sync_returns_actionable_error_when_token_is_still_invalid_after_refresh(
        self,
        mock_get_user,
        mock_refresh_token,
    ):
        mock_get_user.side_effect = [
            MetaAPIError("Session key invalid."),
            MetaAPIError("Session key invalid."),
        ]
        mock_refresh_token.return_value = {
            "access_token": "fresh_token",
            "expires_in": 5184000,
        }

        response = self.client.get(reverse("instagram_sync"))

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("Please reconnect Instagram", payload.get("message", ""))

    @patch("instagram_connect.views.InstagramAnalyticsService.fetch_and_cache")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.refresh_long_lived_access_token")
    @patch("instagram_connect.views.get_instagram_user")
    def test_sync_falls_back_to_exchange_when_forced_refresh_fails(
        self,
        mock_get_user,
        mock_refresh_token,
        mock_exchange_long_lived,
        mock_fetch_and_cache,
    ):
        mock_refresh_token.side_effect = MetaAPIError("Cannot refresh this token.")
        mock_exchange_long_lived.return_value = {
            "access_token": "upgraded_token",
            "expires_in": 5184000,
        }
        mock_get_user.return_value = {
            "id": "123456789",
            "username": "after_upgrade",
            "followers_count": 111,
            "media_count": 13,
        }
        mock_fetch_and_cache.return_value = {
            "account": {"followers_count": 222, "media_count": 23}
        }

        response = self.client.get(reverse("instagram_sync"))

        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.access_token, "upgraded_token")

    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.refresh_long_lived_access_token")
    def test_sync_returns_actionable_error_when_forced_refresh_fails_with_invalid_session(
        self,
        mock_refresh_token,
        mock_exchange_long_lived,
    ):
        mock_refresh_token.side_effect = MetaAPIError("Session key invalid.")
        mock_exchange_long_lived.side_effect = MetaAPIError("Session key invalid.")

        response = self.client.get(reverse("instagram_sync"))

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertIn("Please reconnect Instagram", payload.get("message", ""))
