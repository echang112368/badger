import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import django
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "random_links.settings")
django.setup()

from accounts.models import CustomUser
from merchants.models import MerchantMeta

from .middleware import ShopifySessionTokenMiddleware
from .oauth import validate_shopify_hmac
from .shopify_client import ShopifyClient, ShopifyGraphQLError
from .webhooks import verify_webhook


class OAuthHMACTests(SimpleTestCase):
    @override_settings(SHOPIFY_API_SECRET="topsecret")
    def test_validate_hmac_valid(self):
        params = {"shop": "test.myshopify.com"}
        message = "shop=test.myshopify.com"
        digest = hmac.new(b"topsecret", message.encode(), hashlib.sha256).hexdigest()
        params["hmac"] = digest
        self.assertTrue(validate_shopify_hmac(params))

    @override_settings(SHOPIFY_API_SECRET="topsecret")
    def test_validate_hmac_invalid(self):
        params = {"shop": "test.myshopify.com", "hmac": "invalid"}
        self.assertFalse(validate_shopify_hmac(params))


class SessionTokenMiddlewareTests(SimpleTestCase):
    def _jwks(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
        jwk_data = json.loads(jwk)
        jwk_data["kid"] = "test"
        return key, json.dumps({"keys": [jwk_data]})

    def test_middleware_accepts_valid_token(self):
        key, jwks = self._jwks()
        token = jwt.encode(
            {"dest": "https://test.myshopify.com", "aud": "key", "exp": 9999999999},
            key,
            algorithm="RS256",
            headers={"kid": "test"},
        )
        rf = RequestFactory()
        request = rf.get("/", HTTP_AUTHORIZATION=f"Bearer {token}")

        middleware = ShopifySessionTokenMiddleware(lambda req: json.dumps(getattr(req, "shop_domain", "")))
        with override_settings(SHOPIFY_APP_JWKS=jwks, SHOPIFY_API_KEY="key"):
            response = middleware(request)
        self.assertEqual(response, "\"test.myshopify.com\"")

    def test_middleware_rejects_missing_header(self):
        rf = RequestFactory()
        request = rf.get("/")
        middleware = ShopifySessionTokenMiddleware(lambda req: None)
        response = middleware(request)
        self.assertEqual(response.status_code, 401)


class GraphQLClientTests(SimpleTestCase):
    def test_graphql_retries_and_returns_payload(self):
        calls = []

        class FakeResponse:
            def __init__(self, status_code, payload, headers=None):
                self.status_code = status_code
                self._payload = payload
                self.headers = headers or {}

            def json(self):
                return self._payload

            def raise_for_status(self):
                return None

        def fake_request(method, url, headers=None, **kwargs):
            calls.append((method, url))
            if len(calls) == 1:
                return FakeResponse(429, {"errors": []}, {"Retry-After": "0"})
            return FakeResponse(200, {"data": {"ok": True}})

        with patch("shopify_app.shopify_client.requests.request", side_effect=fake_request):
            client = ShopifyClient("token", "shop")
            payload = client.graphql("{ shop { name } }")
        self.assertEqual(payload["data"], {"ok": True})
        self.assertGreaterEqual(len(calls), 2)

    def test_graphql_raises_on_errors(self):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {"errors": ["bad"]}

            def raise_for_status(self):
                return None

        with patch("shopify_app.shopify_client.requests.request", return_value=FakeResponse()):
            client = ShopifyClient("token", "shop")
            with self.assertRaises(ShopifyGraphQLError):
                client.graphql("query")


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"], SHOPIFY_API_SECRET="secret")
class BillingEnforcementTests(TestCase):
    @patch("shopify_app.views.graphql")
    def test_billing_return_requires_active(self, mock_graphql):
        user = CustomUser.objects.create(username="billing", email="b@example.com")
        meta = MerchantMeta.objects.create(
            user=user,
            shopify_store_domain="shop.myshopify.com",
            shopify_access_token="token",
        )
        mock_graphql.return_value = {"data": {"appSubscription": {"status": "ACTIVE"}}}

        params = {"shop": "shop.myshopify.com", "charge_id": "gid://shopify/AppSubscription/1"}
        message = "charge_id=gid://shopify/AppSubscription/1&shop=shop.myshopify.com"
        hmac_value = hmac.new(b"secret", message.encode(), hashlib.sha256).hexdigest()
        params["hmac"] = hmac_value
        url = reverse("shopify_billing_return") + "?" + "&".join(f"{k}={v}" for k, v in params.items())

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        meta.refresh_from_db()
        self.assertEqual(meta.shopify_billing_status.lower(), "active")


class WebhookVerificationTests(SimpleTestCase):
    @override_settings(SHOPIFY_API_SECRET="secret")
    def test_verify_webhook(self):
        body = json.dumps({"id": 1}).encode()
        digest = hmac.new(b"secret", body, hashlib.sha256).digest()
        hmac_value = base64.b64encode(digest).decode()
        rf = RequestFactory()
        request = rf.post(
            "/webhook",
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_TOPIC="app/uninstalled",
            HTTP_X_SHOPIFY_SHOP_DOMAIN="shop.myshopify.com",
            HTTP_X_SHOPIFY_HMAC_SHA256=hmac_value,
        )
        event = verify_webhook(request)
        self.assertEqual(event["payload"], {"id": 1})


@override_settings(ALLOWED_HOSTS=["testserver", "localhost"])
class DiscountCreationTests(TestCase):
    @patch("shopify_app.views.graphql")
    def test_discount_creation_mutation(self, mock_graphql):
        user = CustomUser.objects.create(username="merchant", email="m@example.com")
        meta = MerchantMeta.objects.create(
            user=user,
            uuid=uuid.uuid4(),
            shopify_store_domain="shop.myshopify.com",
            shopify_access_token="token",
        )
        mock_graphql.return_value = {"data": {"discountAutomaticAppCreate": {"userErrors": []}}}

        url = reverse("create_discount", args=[meta.uuid])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "SUCCESS")
        called_variables = mock_graphql.call_args.args[3]
        self.assertIn("automaticAppDiscount", called_variables)
