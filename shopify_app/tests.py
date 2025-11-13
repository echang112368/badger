"""Tests for the Shopify integration."""

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import uuid

import jwt

from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse
from accounts.models import CustomUser
from merchants.models import MerchantMeta
from rest_framework_simplejwt.tokens import RefreshToken
from unittest.mock import ANY, MagicMock, patch

from . import billing
from .views import _session_token_key


class CreateDiscountViewTests(TestCase):
    def setUp(self):
        user = CustomUser.objects.create_user(
            username="merchant",
            password="pass",
            email="merchant@example.com",
        )
        self.meta = MerchantMeta.objects.create(
            user=user,
            shopify_access_token="token",
            shopify_store_domain="example.myshopify.com",
        )
        self.token = str(RefreshToken.for_user(user).access_token)

    @patch("shopify_app.views.select_discount_percentage", return_value=None)
    @patch("shopify_app.views.ShopifyClient")
    def test_no_discount(self, mock_client_cls, mock_select):
        url = reverse("create_discount", args=[self.meta.uuid])
        response = self.client.post(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"discount": None, "message": "No discount awarded"}
        )
        mock_client_cls.assert_not_called()

    @patch("shopify_app.views.select_discount_percentage", return_value=10)
    @patch("shopify_app.views.uuid.uuid4")
    @patch("shopify_app.views.ShopifyClient")
    def test_discount_created(self, mock_client_cls, mock_uuid4, mock_select):
        mock_uuid4.return_value = uuid.UUID("87654321876543218765432187654321")
        mock_client = mock_client_cls.return_value

        price_rule_response = MagicMock()
        price_rule_response.json.return_value = {"price_rule": {"id": 222}}
        discount_response = MagicMock()
        discount_response.json.return_value = {
            "discount_code": {"code": "BADGER-87654321"}
        }
        mock_client.post.side_effect = [price_rule_response, discount_response]

        url = reverse("create_discount", args=[self.meta.uuid])
        response = self.client.post(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"coupon_code": "BADGER-87654321", "discount": 10}
        )

        first_call = mock_client.post.call_args_list[0]
        rule_payload = first_call.kwargs["json"]["price_rule"]
        self.assertEqual(rule_payload["value"], "-10.0")
        start = datetime.fromisoformat(rule_payload["starts_at"])
        end = datetime.fromisoformat(rule_payload["ends_at"])
        self.assertEqual(end - start, timedelta(days=1))

        mock_client.post.assert_any_call(
            "/admin/api/2024-07/price_rules/222/discount_codes.json",
            json=ANY,
        )


class ShopifyBillingTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="billing",
            password="pass",
            email="billing@example.com",
        )
        self.meta = MerchantMeta.objects.create(
            user=self.user,
            shopify_access_token="token",
            shopify_store_domain="example.myshopify.com",
            monthly_fee=Decimal("30.00"),
        )

    @patch("shopify_app.billing.ShopifyClient")
    def test_create_recurring_charge_updates_meta(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "recurring_application_charge": {
                "id": 123,
                "status": "pending",
                "confirmation_url": "https://confirm",
                "terms": "Usage terms",
                "capped_amount": "500.00",
                "price": "30.00",
                "currency": "USD",
            }
        }
        mock_client_cls.return_value.post.return_value = mock_response

        result = billing.create_or_update_recurring_charge(
            self.meta, return_url="https://return"
        )

        self.meta.refresh_from_db()
        self.assertEqual(self.meta.shopify_recurring_charge_id, "123")
        self.assertEqual(self.meta.shopify_billing_status, "pending")
        self.assertEqual(self.meta.shopify_usage_terms, "Usage terms")
        self.assertEqual(result["id"], 123)

    def test_ensure_active_charge_requires_status(self):
        self.meta.shopify_recurring_charge_id = ""
        self.meta.shopify_billing_status = "pending"
        self.meta.save()

        with self.assertRaises(billing.ShopifyBillingError):
            billing.ensure_active_charge(self.meta)

    @patch("shopify_app.billing.ShopifyClient")
    def test_create_usage_charge(self, mock_client_cls):
        self.meta.shopify_recurring_charge_id = "999"
        self.meta.shopify_billing_status = "active"
        self.meta.save()

        mock_response = MagicMock()
        mock_response.json.return_value = {"usage_charge": {"id": 55}}
        mock_client = mock_client_cls.return_value
        mock_client.post.return_value = mock_response

        details = billing.create_usage_charge(
            self.meta,
            amount=Decimal("10.25"),
            description="Test charge",
        )

        mock_client.post.assert_called_once()
        path = mock_client.post.call_args[0][0]
        self.assertIn("usage_charges", path)
        self.assertEqual(details.charge_id, "55")
        self.assertEqual(details.amount, Decimal("10.25"))


class MerchantInvoiceAdminTests(TestCase):
    def setUp(self):
        self.staff = CustomUser.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        self.client.force_login(self.staff)

    @patch("ledger.admin.generate_all_invoices")
    def test_generate_all_triggers_message(self, mock_generate):
        mock_generate.return_value = [MagicMock(), MagicMock()]

        url = reverse("admin:ledger_invoice_generate_all")
        response = self.client.post(url, follow=True)

        mock_generate.assert_called_once_with(ignore_date=True)

        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("Generated 2 invoice(s) or Shopify charges" in message for message in messages)
        )

@override_settings(SHOPIFY_API_SECRET="shh", SHOPIFY_API_KEY="key")
class EmbeddedAppHomeTests(TestCase):
    def setUp(self):
        self.shop_domain = "example.myshopify.com"
        self.access_token = "shpua_token"

    def _store_session_token(self):
        session = self.client.session
        session[_session_token_key(self.shop_domain)] = self.access_token
        session.save()

    def _signed_params(self, **params):
        base = {"shop": self.shop_domain, "timestamp": "1234567890"}
        base.update(params)
        message = "&".join(
            f"{key}={value}"
            for key, value in sorted(base.items())
            if key != "hmac"
        )
        digest = hmac.new(b"shh", message.encode("utf-8"), hashlib.sha256).hexdigest()
        base["hmac"] = digest
        return base

    def test_get_requires_valid_signature(self):
        url = reverse("shopify_embedded_home")
        params = {
            "shop": self.shop_domain,
            "timestamp": "1234567890",
            "hmac": "bad",
        }
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 400)

        self._store_session_token()
        response = self.client.get(url, self._signed_params())
        self.assertEqual(response.status_code, 200)

    def test_signup_links_shopify_store(self):
        url = reverse("shopify_embedded_home")
        self._store_session_token()
        self.client.get(url, self._signed_params())

        post_data = {
            "action": "signup",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "company_name": "Ada Co",
            "password1": "supersafe123",
            "password2": "supersafe123",
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("merchant_dashboard"))

        user = CustomUser.objects.get(email="ada@example.com")
        self.assertTrue(user.is_merchant)

        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_store_domain, self.shop_domain)
        self.assertEqual(meta.shopify_access_token, self.access_token)
        self.assertEqual(meta.company_name, "Ada Co")

        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)
        self.assertNotIn(_session_token_key(self.shop_domain), self.client.session)

    def test_login_attaches_existing_user(self):
        user = CustomUser.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="pass12345",
        )

        MerchantMeta.objects.create(
            user=user,
            shopify_store_domain=self.shop_domain,
            shopify_access_token=self.access_token,
        )

        url = reverse("shopify_embedded_home")
        self.client.get(url, self._signed_params())

        response = self.client.post(
            url,
            {
                "action": "login",
                "username": "merchant@example.com",
                "password": "pass12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("merchant_dashboard"))

        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_store_domain, self.shop_domain)
        self.assertEqual(meta.shopify_access_token, self.access_token)
        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)


@override_settings(SHOPIFY_API_SECRET="shh", SHOPIFY_API_KEY="key")
class OAuthCallbackTests(TestCase):
    def setUp(self):
        self.url = reverse("shopify_oauth_callback")
        self.shop_domain = "example.myshopify.com"
        self.access_token = "shppa_token"

    def _build_id_token(self, **extra_claims):
        now = datetime.utcnow()
        payload = {
            "iss": "https://example.myshopify.com/admin",
            "dest": "https://example.myshopify.com",
            "aud": "key",
            "sub": "1",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "nbf": int((now - timedelta(minutes=1)).timestamp()),
            "iat": int(now.timestamp()),
            "jti": "session-token",
        }
        payload.update(extra_claims)
        token = jwt.encode(payload, "shh", algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token

    def test_session_token_logs_in_existing_merchant(self):
        user = CustomUser.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="pass12345",
        )
        MerchantMeta.objects.create(
            user=user,
            shopify_store_domain=self.shop_domain,
            shopify_access_token=self.access_token,
        )

        response = self.client.get(self.url, {"id_token": self._build_id_token()})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("merchant_dashboard"))
        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)

    def test_session_token_redirects_to_onboarding_when_unknown(self):
        response = self.client.get(self.url, {"id_token": self._build_id_token()})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/onboard/?shop={self.shop_domain}")
        self.assertEqual(
            self.client.session.get("shopify_pending_shop"), self.shop_domain
        )

    @override_settings(SHOPIFY_SESSION_TOKEN_LEEWAY=30)
    def test_session_token_allows_for_small_clock_skew(self):
        future_nbf = int((datetime.utcnow() + timedelta(seconds=20)).timestamp())
        token = self._build_id_token(nbf=future_nbf)

        response = self.client.get(self.url, {"id_token": token})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/onboard/?shop={self.shop_domain}")

    @patch("shopify_app.views._exchange_code_for_token", return_value="shppa_token")
    @patch("shopify_app.views._validate_shopify_hmac", return_value=True)
    def test_oauth_callback_stores_session_token(self, mock_hmac, mock_exchange):
        session = self.client.session
        session["shopify_oauth_state"] = "abc"
        session.save()

        response = self.client.get(
            self.url,
            {"shop": self.shop_domain, "code": "abc", "hmac": "valid", "state": "abc"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(_session_token_key(self.shop_domain), self.client.session)

    def test_invalid_session_token_returns_400(self):
        bad_token = jwt.encode({"iss": "bad"}, "wrong", algorithm="HS256")
        if isinstance(bad_token, bytes):
            bad_token = bad_token.decode("utf-8")

        response = self.client.get(self.url, {"id_token": bad_token})

        self.assertEqual(response.status_code, 400)
