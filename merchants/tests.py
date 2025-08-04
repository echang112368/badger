from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from .models import MerchantMeta


class MerchantSettingsTests(TestCase):
    def test_saves_shopify_credentials(self):
        user = CustomUser.objects.create_user(
            username="merchant", password="pass", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "paypal_email": "merchant@example.com",
                "shopify_api_key": "key",
                "shopify_api_password": "password",
                "shopify_store_domain": "example.myshopify.com",
            },
        )

        self.assertRedirects(response, reverse("merchant_settings"))

        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_api_key, "key")
        self.assertEqual(meta.shopify_api_password, "password")
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")

