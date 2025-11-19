from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from .models import MerchantMeta
from decimal import Decimal
from unittest.mock import patch

from .forms import ItemGroupForm, MerchantSettingsForm
from shopify_app import billing as shopify_billing


class MerchantSettingsFormTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="merchant_form",
            password="pass",
            email="form@example.com",
            is_merchant=True,
        )

    def test_requires_paypal_for_independent(self):
        form = MerchantSettingsForm(
            data={
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
                "paypal_email": "",
                "shopify_store_domain": "",
            },
            instance=self.user.merchantmeta,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("paypal_email", form.errors)

    def test_allows_shopify_without_credentials(self):
        form = MerchantSettingsForm(
            data={
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "paypal_email": "",
                "shopify_store_domain": "",
            },
            instance=self.user.merchantmeta,
        )
        self.assertTrue(form.is_valid())

    def test_normalizes_shopify_store_domain(self):
        form = MerchantSettingsForm(
            data={
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "paypal_email": "",
                "shopify_store_domain": "https://Example.myshopify.com/",
            },
            instance=self.user.merchantmeta,
        )
        self.assertTrue(form.is_valid())
        meta = form.save()
        self.user.refresh_from_db()
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")


class MerchantSettingsTests(TestCase):
    def test_updates_shopify_store_domain(self):
        user = CustomUser.objects.create_user(
            username="merchant", password="pass", email="merchant1@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "paypal_email": "merchant@example.com",
                "shopify_store_domain": "https://Example.myshopify.com/",
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
            },
        )

        self.assertRedirects(response, reverse("merchant_settings"))

        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")


    def test_settings_displays_email(self):
        user = CustomUser.objects.create_user(
            username="merchant", password="pass", email="merchant@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("merchant_settings"))
        self.assertContains(response, user.email)

    def test_settings_displays_password(self):
        user = CustomUser.objects.create_user(
            username="merchant3", password="pass123", email="merchant3@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.get(reverse("merchant_settings"))
        self.assertContains(response, user.password)

    def test_settings_displays_uuid(self):
        user = CustomUser.objects.create_user(
            username="merchant_uuid",
            password="pass123",
            email="merchant_uuid@example.com",
            is_merchant=True,
        )
        merchant_meta = MerchantMeta.objects.get(user=user)
        self.client.force_login(user)
        response = self.client.get(reverse("merchant_settings"))
        self.assertContains(response, str(merchant_meta.uuid))

    def test_settings_updates_name(self):
        user = CustomUser.objects.create_user(
            username="merchant4", password="pass123", email="merchant4@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "first_name": "New",
                "last_name": "Name",
                "paypal_email": "merchant4@example.com",
                "shopify_store_domain": "",
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
            },
        )
        self.assertRedirects(response, reverse("merchant_settings"))
        user.refresh_from_db()
        self.assertEqual(user.first_name, "New")


    def test_redirect_preserves_active_tab(self):
        user = CustomUser.objects.create_user(
            username="merchant_tabs",
            password="pass123",
            email="merchant_tabs@example.com",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.shopify_access_token = "existing-token"
        meta.shopify_store_domain = "tabstore.myshopify.com"
        meta.save()
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "company_name": "",
                "paypal_email": "",
                "shopify_store_domain": "https://TabStore.myshopify.com/",
                "shopify_oauth_authorization_line": "scope=read_products;connected_at=2024-01-01T00:00:00",
                "first_name": "",
                "last_name": "",
                "active_tab": "api",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
            },
        )
        self.assertRedirects(
            response, f"{reverse('merchant_settings')}?tab=api"
        )
        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_store_domain, "tabstore.myshopify.com")


    def test_saves_settings_when_user_form_invalid(self):
        user = CustomUser.objects.create_user(
            username="merchant_partial",
            password="pass123",
            email="merchant_partial@example.com",
            is_merchant=True,
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "company_name": "",
                "paypal_email": "",
                "shopify_store_domain": "partial-store.myshopify.com",
                "shopify_oauth_authorization_line": "scope=write_discounts;connected_at=2024-01-01T00:00:00",
                "first_name": "A" * 200,
                "last_name": "",
                "active_tab": "api",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
            },
        )
        self.assertEqual(response.status_code, 200)
        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(
            response.context["active_tab"], "api"
        )
        self.assertIn("first_name", response.context["user_form"].errors)

    def test_enabling_shopify_without_token_redirects_to_oauth(self):
        user = CustomUser.objects.create_user(
            username="merchant_shopify",
            password="pass123",
            email="merchant_shopify@example.com",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.INDEPENDENT
        meta.shopify_access_token = ""
        meta.shopify_store_domain = ""
        meta.save()

        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "company_name": "",
                "paypal_email": "",
                "shopify_store_domain": "https://Example.myshopify.com/",
                "first_name": "",
                "last_name": "",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
            },
        )

        expected_url = (
            f"{reverse('shopify_oauth_authorize')}?shop=example.myshopify.com"
        )
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
        )

    @patch("merchants.views.shopify_billing.create_or_update_recurring_charge")
    def test_start_shopify_billing(self, mock_create):
        user = CustomUser.objects.create_user(
            username="shopify", password="pass", email="shopify@example.com", is_merchant=True
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_access_token = "token"
        meta.shopify_store_domain = "shop.test"
        meta.monthly_fee = Decimal("25.00")
        meta.save()

        mock_create.return_value = {"id": 1, "status": "pending", "capped_amount": "100.00"}

        self.client.force_login(user)
        response = self.client.post(reverse("merchant_start_shopify_billing"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        mock_create.assert_called_once()

    @patch("merchants.views.shopify_billing.create_or_update_recurring_charge")
    def test_start_shopify_billing_prompts_reauth(self, mock_create):
        user = CustomUser.objects.create_user(
            username="shopify_reauth",
            password="pass",
            email="shopifyreauth@example.com",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_access_token = "token"
        meta.shopify_store_domain = "shop.test"
        meta.monthly_fee = Decimal("25.00")
        meta.save()

        mock_create.side_effect = shopify_billing.ShopifyReauthorizationRequired(
            "shop.test"
        )

        self.client.force_login(user)
        response = self.client.post(reverse("merchant_start_shopify_billing"))
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertIn("authorize_url", data)
        self.assertIn("error", data)

    def test_start_shopify_billing_requires_shopify_type(self):
        user = CustomUser.objects.create_user(
            username="shopify_type", password="pass", email="shopifytype@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(reverse("merchant_start_shopify_billing"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())


class MerchantDashboardTests(TestCase):
    def test_shopify_merchant_without_token_redirects_to_oauth(self):
        user = CustomUser.objects.create_user(
            username="dashboard_oauth", password="pass123", email="dashboard@example.com", is_merchant=True
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_store_domain = "https://Example.myshopify.com/"
        meta.shopify_access_token = ""
        meta.save()

        self.client.force_login(user)
        response = self.client.get(reverse("merchant_dashboard"))

        expected_url = f"{reverse('shopify_oauth_authorize')}?shop=example.myshopify.com"
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
        )

    def test_shopify_merchant_with_credentials_can_view_dashboard(self):
        user = CustomUser.objects.create_user(
            username="dashboard_ready", password="pass123", email="ready@example.com", is_merchant=True
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_store_domain = "ready.myshopify.com"
        meta.shopify_access_token = "token"
        meta.save()

        self.client.force_login(user)
        response = self.client.get(reverse("merchant_dashboard"))

        self.assertEqual(response.status_code, 200)


class StoreIdLookupTests(TestCase):
    def test_returns_uuid_for_domain(self):
        user = CustomUser.objects.create_user(
            username="merchant2", password="pass", email="merchant2@example.com", is_merchant=True
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.shopify_store_domain = "https://Example.myshopify.com/"
        meta.save()

        url = reverse("merchant_store_id") + "?domain=example.myshopify.com"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"storeID": str(meta.uuid)})

    def test_returns_null_for_unknown_domain(self):
        url = reverse("merchant_store_id") + "?domain=unknown.com"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"storeID": None})


class ItemGroupFormTests(TestCase):
    def test_affiliate_percent_required(self):
        merchant = CustomUser.objects.create_user(
            username="merchant_group",
            password="pass",
            email="merchant_group@example.com",
            is_merchant=True,
        )
        form = ItemGroupForm(data={"name": "Group"}, merchant=merchant)
        self.assertFalse(form.is_valid())
        self.assertIn("affiliate_percent", form.errors)

