from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from instagram_connect.models import InstagramConnection
from instagram_connect.services import (
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
            "instagram_business_basic,instagram_business_manage_comments,"
            "instagram_business_manage_comments"
        )
    )
    def test_resolve_meta_oauth_scopes_deduplicates_string_value(self):
        self.assertEqual(
            resolve_meta_oauth_scopes(),
            "instagram_business_basic,instagram_business_manage_comments",
        )

    @override_settings(
        META_OAUTH_SCOPES=[
            "instagram_business_basic",
            "instagram_business_manage_messages",
            "instagram_business_basic",
        ]
    )
    def test_resolve_meta_oauth_scopes_deduplicates_iterables(self):
        self.assertEqual(
            resolve_meta_oauth_scopes(),
            "instagram_business_basic,instagram_business_manage_messages",
        )

    @override_settings(META_APP_ID="app_123", META_REDIRECT_URI="https://example.com/callback")
    def test_build_oauth_url_uses_configured_scopes(self):
        with override_settings(
            META_OAUTH_SCOPES=["instagram_business_basic", "instagram_business_content_publish"],
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
            ["instagram_business_basic,instagram_business_content_publish"],
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
                    "permissions": "instagram_business_basic",
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
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_redirects_to_settings_with_success_flag(self, mock_exchange, mock_get_user):
        mock_exchange.return_value = {"access_token": "token_123", "expires_in": 5184000}
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

    def test_callback_redirects_to_settings_with_error_flag_for_oauth_error(self):
        response = self.client.get(
            reverse("instagram_callback"),
            {"error": "access_denied", "state": "state_abc"},
        )

        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_oauth=error")
        self.assertFalse(InstagramConnection.objects.filter(user=self.user).exists())
