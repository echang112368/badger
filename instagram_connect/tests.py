from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from instagram_connect.services import (
    build_oauth_url,
    exchange_code_for_access_token,
    get_instagram_user,
    resolve_meta_oauth_scopes,
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
