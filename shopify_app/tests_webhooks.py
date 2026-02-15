import base64
import hashlib
import hmac

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from shopify_app.webhook_verification import is_valid_shopify_webhook


class ShopifyWebhookVerificationTests(SimpleTestCase):
    def _signature(self, body: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    @override_settings(SHOPIFY_API_SECRET="top-secret")
    def test_accepts_valid_hmac_from_primary_secret(self):
        body = b'{"hello":"world"}'
        signature = self._signature(body, "top-secret")

        response = self.client.post(
            reverse("shopify_customers_data_request_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
            HTTP_X_SHOPIFY_SHOP_DOMAIN="demo-shop.myshopify.com",
        )

        self.assertEqual(response.status_code, 200)

    @override_settings(SHOPIFY_API_SECRET="wrong", SHOPIFY_WEBHOOK_SECRETS="old-secret,new-secret")
    def test_accepts_rotated_webhook_secret(self):
        body = b'{"check":"rotation"}'
        signature = self._signature(body, "new-secret")

        request = self.client.post(
            reverse("shopify_customers_redact_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
        ).wsgi_request

        self.assertTrue(is_valid_shopify_webhook(request))

    @override_settings(SHOPIFY_API_SECRET="top-secret")
    def test_rejects_invalid_hmac(self):
        body = b'{}'
        response = self.client.post(
            reverse("shopify_shop_redact_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256="invalid",
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(SHOPIFY_API_SECRET="")
    def test_rejects_missing_secret_configuration(self):
        body = b'{"id":1}'
        signature = self._signature(body, "top-secret")
        response = self.client.post(
            reverse("shopify_shop_redact_webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
        )

        self.assertEqual(response.status_code, 401)
