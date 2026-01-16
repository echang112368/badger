from django.test import TestCase, override_settings
import json

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
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
            instance=self.user.merchantmeta,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("paypal_email", form.errors)

    def test_allows_shopify_without_credentials(self):
        meta = self.user.merchantmeta
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.save()

        form = MerchantSettingsForm(
            data={
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "paypal_email": "",
                "shopify_store_domain": "",
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
            instance=self.user.merchantmeta,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("shopify_store_domain", form.errors)

    def test_normalizes_shopify_store_domain(self):
        meta = self.user.merchantmeta
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.save()

        form = MerchantSettingsForm(
            data={
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "paypal_email": "",
                "shopify_store_domain": "https://Example.myshopify.com/",
                "billing_plan": MerchantMeta.BillingPlan.PLATFORM_ONLY,
            },
            instance=meta,
        )
        self.assertTrue(form.is_valid())
        meta = form.save()
        self.user.refresh_from_db()
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")

    def test_business_type_cannot_be_changed(self):
        meta = self.user.merchantmeta
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.save()

        form = MerchantSettingsForm(
            data={
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
                "paypal_email": "merchant@example.com",
                "shopify_store_domain": "example.myshopify.com",
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
            instance=meta,
        )

        self.assertTrue(form.is_valid())
        saved = form.save()
        self.assertEqual(saved.business_type, MerchantMeta.BusinessType.SHOPIFY)

    def test_business_type_defaults_to_existing_when_omitted(self):
        meta = self.user.merchantmeta
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.save()

        form = MerchantSettingsForm(
            data={
                "paypal_email": "merchant@example.com",
                "shopify_store_domain": "example.myshopify.com",
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
            instance=meta,
        )

        self.assertTrue(form.is_valid())
        saved = form.save()
        self.assertEqual(saved.business_type, MerchantMeta.BusinessType.SHOPIFY)


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
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
        )

        self.assertRedirects(response, reverse("merchant_settings"))

        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")


    def test_rejects_invalid_shopify_store_domain(self):
        user = CustomUser.objects.create_user(
            username="merchant_invalid", password="pass", email="merchant_invalid@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "paypal_email": "merchant_invalid@example.com",
                "shopify_store_domain": "shop.test",
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["settings_form"]
        self.assertIn("shopify_store_domain", form.errors)

    def test_shopify_store_domain_locked_for_shopify_merchants(self):
        user = CustomUser.objects.create_user(
            username="merchant_locked", password="pass", email="merchant_locked@example.com", is_merchant=True
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_store_domain = "locked.myshopify.com"
        meta.shopify_access_token = "token"
        meta.save()

        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_settings"),
            {
                "paypal_email": "merchant_locked@example.com",
                "shopify_store_domain": "new-shop.myshopify.com",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
        )

        self.assertRedirects(response, reverse("merchant_settings"))
        meta.refresh_from_db()
        self.assertEqual(meta.shopify_store_domain, "locked.myshopify.com")


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
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, user.password)

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
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
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
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
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
                "first_name": "",
                "last_name": "",
                "active_tab": "api",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
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
                "first_name": "A" * 200,
                "last_name": "",
                "active_tab": "api",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
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
                "billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR,
            },
        )
        meta.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(meta.business_type, MerchantMeta.BusinessType.INDEPENDENT)

    @patch("merchants.views.register_orders_create_webhook")
    @patch("merchants.views.shopify_billing.create_or_update_recurring_charge")
    def test_start_shopify_billing(self, mock_create, mock_register_webhook):
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
        mock_register_webhook.return_value = True

        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_start_shopify_billing"),
            data=json.dumps({"billing_plan": MerchantMeta.BillingPlan.PLATFORM_ONLY}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        expected_return_url = f"http://testserver{reverse('shopify_billing_return')}?shop=shop.test"
        mock_create.assert_called_once_with(meta, return_url=expected_return_url)
        meta.refresh_from_db()
        self.assertEqual(meta.billing_plan, MerchantMeta.BillingPlan.PLATFORM_ONLY)
        self.assertEqual(meta.monthly_fee, meta.plan_price)

    @override_settings(
        SHOPIFY_USAGE_CAPPED_AMOUNT=Decimal("500.00"),
        SHOPIFY_USAGE_TERMS="Usage-based charges",
    )
    @patch("merchants.views.register_orders_create_webhook")
    @patch("merchants.views.shopify_billing.create_or_update_recurring_charge")
    def test_start_shopify_billing_sets_usage_defaults(self, mock_create, mock_register_webhook):
        user = CustomUser.objects.create_user(
            username="shopify_usage",
            password="pass",
            email="shopify_usage@example.com",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_access_token = "token"
        meta.shopify_store_domain = "shop.test"
        meta.monthly_fee = Decimal("30.00")
        meta.shopify_usage_capped_amount = None
        meta.shopify_usage_terms = ""
        meta.save()

        mock_create.return_value = {"id": 1, "status": "pending", "capped_amount": "500.00"}
        mock_register_webhook.return_value = True

        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_start_shopify_billing"),
            data=json.dumps({"billing_plan": MerchantMeta.BillingPlan.BADGER_CREATOR}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        meta.refresh_from_db()
        self.assertEqual(meta.shopify_usage_capped_amount, Decimal("500.00"))
        self.assertEqual(meta.shopify_usage_terms, "Usage-based charges")

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

    def test_start_shopify_billing_requires_shop_domain(self):
        user = CustomUser.objects.create_user(
            username="shopify_missing_shop",
            password="pass",
            email="shopify_missing@example.com",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_access_token = "token"
        meta.shopify_store_domain = ""
        meta.save()

        self.client.force_login(user)
        response = self.client.post(reverse("merchant_start_shopify_billing"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Shopify store domain is required", response.json().get("error", ""))

    def test_start_shopify_billing_requires_shopify_type(self):
        user = CustomUser.objects.create_user(
            username="shopify_type", password="pass", email="shopifytype@example.com", is_merchant=True
        )
        self.client.force_login(user)
        response = self.client.post(reverse("merchant_start_shopify_billing"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_start_shopify_billing_rejects_invalid_plan(self):
        user = CustomUser.objects.create_user(
            username="shopify_invalid_plan",
            password="pass",
            email="shopifyinvalid@example.com",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_access_token = "token"
        meta.shopify_store_domain = "shop.test"
        meta.save()

        self.client.force_login(user)
        response = self.client.post(
            reverse("merchant_start_shopify_billing"),
            data=json.dumps({"billing_plan": "not-a-plan"}),
            content_type="application/json",
        )
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
        self.assertIn("return_policy_days", form.errors)
