from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from .models import MerchantMeta


class MerchantSettingsTests(TestCase):
    def test_saves_shopify_token(self):
        user = CustomUser.objects.create_user(
            username="merchant", password="pass", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "paypal_email": "merchant@example.com",
                "shopify_access_token": "token",
                "shopify_store_domain": "example.myshopify.com",
            },
        )

        self.assertRedirects(response, reverse("merchant_settings"))

        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_access_token, "token")
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")


class StoreIdLookupTests(TestCase):
    def test_returns_uuid_for_domain(self):
        user = CustomUser.objects.create_user(
            username="merchant2", password="pass", is_merchant=True
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.shopify_store_domain = "example.myshopify.com"
        meta.save()

        url = reverse("merchant_store_id") + "?domain=example.myshopify.com"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"storeID": str(meta.uuid)})

    def test_returns_404_for_unknown_domain(self):
        url = reverse("merchant_store_id") + "?domain=unknown.com"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

