from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser
from instagram_connect.models import InstagramConnection
from instagram_connect.services import (
    exchange_code_for_access_token,
    get_instagram_user,
    get_page_instagram_business_account,
    get_user_pages,
    resolve_meta_oauth_scopes,
)


class MetaOAuthScopeTests(SimpleTestCase):
    @override_settings(
        META_OAUTH_SCOPES=(
            "instagram_basic,pages_show_list,pages_show_list,instagram_manage_insights"
        )
    )
    def test_resolve_meta_oauth_scopes_deduplicates_string_value(self):
        self.assertEqual(
            resolve_meta_oauth_scopes(),
            "instagram_basic,pages_show_list,instagram_manage_insights",
        )

    @override_settings(META_APP_ID="app_123", META_REDIRECT_URI="https://example.com/callback")
    def test_connect_route_uses_facebook_oauth_dialog(self):
        user = CustomUser.objects.create_user(
            username="oauth_start_user",
            email="oauth_start_user@example.com",
            password="pass123",
            is_creator=True,
        )
        client = self.client
        client.force_login(user)

        response = client.get(reverse("connect_instagram"))

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response.url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "www.facebook.com")
        self.assertEqual(parsed.path, "/dialog/oauth")
        params = parse_qs(parsed.query)
        self.assertEqual(params["client_id"], ["app_123"])
        self.assertEqual(params["redirect_uri"], ["https://example.com/callback"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertTrue(params["state"][0])


class MetaServiceHttpTests(SimpleTestCase):
    @override_settings(
        META_APP_ID="app_123",
        META_APP_SECRET="secret_123",
        META_REDIRECT_URI="https://example.com/callback",
        META_API_VERSION="v22.0",
    )
    @patch("instagram_connect.services.requests.get")
    def test_exchange_code_uses_facebook_graph_endpoint(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"access_token": "token_abc", "expires_in": 3600}
        mock_get.return_value = response

        token_data = exchange_code_for_access_token("code_abc")

        mock_get.assert_called_once_with(
            "https://graph.facebook.com/v22.0/oauth/access_token",
            params={
                "client_id": "app_123",
                "client_secret": "secret_123",
                "redirect_uri": "https://example.com/callback",
                "code": "code_abc",
            },
            timeout=15,
        )
        self.assertEqual(token_data["access_token"], "token_abc")

    @override_settings(META_API_VERSION="v22.0")
    @patch("instagram_connect.services.requests.get")
    def test_get_user_pages_uses_graph_facebook(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "data": [{"id": "page_1", "name": "Page One", "access_token": "page_token"}]
        }
        mock_get.return_value = response

        pages = get_user_pages("user_token")

        mock_get.assert_called_once_with(
            "https://graph.facebook.com/v22.0/me/accounts",
            params={"fields": "id,name,access_token", "access_token": "user_token"},
            timeout=15,
        )
        self.assertEqual(pages[0]["id"], "page_1")

    @override_settings(META_API_VERSION="v22.0")
    @patch("instagram_connect.services.requests.get")
    def test_get_linked_ig_business_account(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "instagram_business_account": {
                "id": "178900000000001",
                "username": "creator",
                "followers_count": 22,
            }
        }
        mock_get.return_value = response

        account = get_page_instagram_business_account("123", "page_token")

        mock_get.assert_called_once_with(
            "https://graph.facebook.com/v22.0/123",
            params={
                "fields": "instagram_business_account{id,username,followers_count,media_count}",
                "access_token": "page_token",
            },
            timeout=15,
        )
        self.assertEqual(account["id"], "178900000000001")

    @override_settings(META_API_VERSION="v22.0")
    @patch("instagram_connect.services.requests.get")
    def test_get_instagram_user_uses_graph_facebook_node(self, mock_get):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"id": "178900000000001", "username": "creator"}
        mock_get.return_value = response

        payload = get_instagram_user("178900000000001", "user_token")

        mock_get.assert_called_once_with(
            "https://graph.facebook.com/v22.0/178900000000001",
            params={
                "fields": "id,username,followers_count,media_count,account_type",
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
        session["meta_oauth_state"] = "state_abc"
        session.save()

    @patch("instagram_connect.views.get_instagram_user")
    @patch("instagram_connect.views.get_page_instagram_business_account")
    @patch("instagram_connect.views.get_user_pages")
    @patch("instagram_connect.views.get_facebook_user")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_persists_meta_connection(
        self,
        mock_exchange,
        mock_exchange_long,
        mock_fb_user,
        mock_pages,
        mock_page_ig,
        mock_get_ig,
    ):
        mock_exchange.return_value = {"access_token": "short_token", "expires_in": 3600}
        mock_exchange_long.return_value = {"access_token": "long_token", "expires_in": 5184000}
        mock_fb_user.return_value = {"id": "fb_1", "name": "Creator FB"}
        mock_pages.return_value = [{"id": "page_1", "name": "Page One", "access_token": "page_token"}]
        mock_page_ig.return_value = {"id": "1789", "username": "creator_name"}
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
        self.assertEqual(connection.facebook_user_id, "fb_1")
        self.assertEqual(connection.page_id, "page_1")
        self.assertEqual(connection.instagram_user_id, "1789")
        self.assertEqual(connection.access_token, "long_token")

    @patch("instagram_connect.views.get_user_pages")
    @patch("instagram_connect.views.get_facebook_user")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_handles_no_pages(
        self,
        mock_exchange,
        mock_exchange_long,
        mock_fb_user,
        mock_pages,
    ):
        mock_exchange.return_value = {"access_token": "short_token", "expires_in": 3600}
        mock_exchange_long.return_value = {"access_token": "long_token", "expires_in": 5184000}
        mock_fb_user.return_value = {"id": "fb_1"}
        mock_pages.return_value = []

        response = self.client.get(
            reverse("instagram_callback"),
            {"code": "code_123", "state": "state_abc"},
        )

        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_oauth=error")
        self.assertFalse(InstagramConnection.objects.filter(user=self.user).exists())

    @patch("instagram_connect.views.get_instagram_user")
    @patch("instagram_connect.views.get_page_instagram_business_account", return_value=None)
    @patch("instagram_connect.views.get_user_pages")
    @patch("instagram_connect.views.get_facebook_user")
    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    @patch("instagram_connect.views.exchange_code_for_access_token")
    def test_callback_handles_page_without_linked_instagram(
        self,
        mock_exchange,
        mock_exchange_long,
        mock_fb_user,
        mock_pages,
        *_args,
    ):
        mock_exchange.return_value = {"access_token": "short_token", "expires_in": 3600}
        mock_exchange_long.return_value = {"access_token": "long_token", "expires_in": 5184000}
        mock_fb_user.return_value = {"id": "fb_1"}
        mock_pages.return_value = [{"id": "page_1", "name": "Page One", "access_token": "page_token"}]

        response = self.client.get(
            reverse("instagram_callback"),
            {"code": "code_123", "state": "state_abc"},
        )

        self.assertRedirects(response, f"{reverse('creator_settings')}?instagram_oauth=error")


class InstagramSyncFailureTests(TestCase):
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
            access_token="stale_token",
        )

    @patch("instagram_connect.views.exchange_for_long_lived_access_token")
    def test_sync_fails_when_refresh_fails_with_permission_error(self, mock_exchange):
        from instagram_connect.services import MetaAPIError

        mock_exchange.side_effect = MetaAPIError("Permissions error")

        response = self.client.get(reverse("instagram_sync"))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "meta_api_error")
