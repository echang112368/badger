"""Tests for the Shopify integration."""

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import uuid
import html

import jwt
from urllib.parse import parse_qs, urlencode, urlparse

from django.contrib.messages import get_messages
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.models import CustomUser
from merchants.models import MerchantMeta
from rest_framework_simplejwt.tokens import RefreshToken
from unittest.mock import ANY, MagicMock, patch

from . import billing, views
from .oauth import (
    CALLBACK_SESSION_KEY,
    STATE_SESSION_KEY,
    AccessTokenResponse,
    ShopifyOAuthError,
    exchange_code_for_token,
    session_refresh_key,
    session_scope_key,
    session_token_key,
)
from .shopify_client import (
    ADMIN_API_VERSION,
    ShopifyClient,
    ShopifyInvalidCredentialsError,
    _PRODUCTS_QUERY,
)
from .token_management import clear_shopify_token_for_shop, refresh_shopify_token
from requests import HTTPError


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

        mock_client.graphql.return_value = {
            "data": {
                "discountCodeBasicCreate": {
                    "codeDiscountNode": {"id": "gid://shopify/DiscountCodeNode/123"},
                    "userErrors": [],
                }
            }
        }

        url = reverse("create_discount", args=[self.meta.uuid])
        response = self.client.post(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"coupon_code": "BADGER-87654321", "discount": 10}
        )

        first_call = mock_client.graphql.call_args_list[0]
        variables = first_call.args[1]
        rule_payload = variables["basicCodeDiscount"]
        self.assertEqual(rule_payload["customerGets"]["value"], {"percentage": 0.1})
        start = datetime.fromisoformat(rule_payload["startsAt"])
        end = datetime.fromisoformat(rule_payload["endsAt"])
        self.assertEqual(end - start, timedelta(days=1))


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
        mock_client_cls.return_value.create_app_subscription.return_value = {
            "confirmation_url": "https://confirm",
            "subscription": {
                "id": "gid://shopify/AppSubscription/123",
                "status": "PENDING",
                "lineItems": [
                    {
                        "id": "gid://shopify/AppSubscriptionLineItem/1",
                        "plan": {
                            "pricingDetails": {
                                "__typename": "AppRecurringPricing",
                                "price": {"amount": "30.00", "currencyCode": "USD"},
                            },
                        },
                    },
                    {
                        "id": "gid://shopify/AppSubscriptionLineItem/2",
                        "plan": {
                            "pricingDetails": {
                                "__typename": "AppUsagePricing",
                                "terms": "Usage terms",
                                "cappedAmount": {
                                    "amount": "500.00",
                                    "currencyCode": "USD",
                                },
                            },
                        },
                    },
                ],
            },
        }

        result = billing.create_or_update_recurring_charge(
            self.meta, return_url="https://return"
        )

        self.meta.refresh_from_db()
        self.assertEqual(self.meta.shopify_recurring_charge_id, "123")
        self.assertEqual(self.meta.shopify_billing_status, "PENDING")
        self.assertEqual(self.meta.shopify_usage_terms, "Usage terms")
        self.assertEqual(result["id"], "123")

    @override_settings(
        SHOPIFY_USAGE_CAPPED_AMOUNT=Decimal("500.00"),
        SHOPIFY_USAGE_TERMS="Usage-based charges",
    )
    @patch("shopify_app.billing.ShopifyClient")
    def test_create_recurring_charge_uses_default_usage_pricing(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        mock_client.create_app_subscription.return_value = {
            "confirmation_url": "https://confirm",
            "subscription": {
                "id": "gid://shopify/AppSubscription/234",
                "status": "PENDING",
                "lineItems": [
                    {
                        "id": "gid://shopify/AppSubscriptionLineItem/1",
                        "plan": {
                            "pricingDetails": {
                                "__typename": "AppRecurringPricing",
                                "price": {"amount": "30.00", "currencyCode": "USD"},
                            },
                        },
                    },
                    {
                        "id": "gid://shopify/AppSubscriptionLineItem/2",
                        "plan": {
                            "pricingDetails": {
                                "__typename": "AppUsagePricing",
                                "terms": "Usage-based charges",
                                "cappedAmount": {
                                    "amount": "500.00",
                                    "currencyCode": "USD",
                                },
                            },
                        },
                    },
                ],
            },
        }

        self.meta.billing_plan = MerchantMeta.BillingPlan.BADGER_CREATOR
        self.meta.shopify_usage_capped_amount = None
        self.meta.shopify_usage_terms = ""
        self.meta.save()

        billing.create_or_update_recurring_charge(self.meta, return_url="https://return")

        mock_client.create_app_subscription.assert_called_once_with(
            plan_name=billing.expected_shopify_plan_name(self.meta),
            price_amount=self.meta.monthly_fee,
            trial_days=0,
            return_url="https://return",
            test_mode=ANY,
            usage_capped_amount=Decimal("500.00"),
            usage_terms="Usage-based charges",
        )

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
        self.meta.shopify_billing_plan = self.meta.billing_plan
        self.meta.save()

        mock_client = mock_client_cls.return_value
        mock_client.graphql.side_effect = [
            {
                "data": {
                    "node": {
                        "__typename": "AppSubscription",
                        "id": "gid://shopify/AppSubscription/999",
                        "lineItems": [
                            {
                                "id": "gid://shopify/AppSubscriptionLineItem/usage",
                                "plan": {
                                    "pricingDetails": {
                                        "__typename": "AppUsagePricing",
                                        "terms": "Usage terms",
                                        "cappedAmount": {
                                            "amount": "500.00",
                                            "currencyCode": "USD",
                                        },
                                    },
                                },
                            }
                        ],
                    },
                }
            },
            {
                "data": {
                    "appUsageRecordCreate": {
                        "appUsageRecord": {
                            "id": "gid://shopify/AppUsageRecord/55",
                            "description": "Test charge",
                            "price": {"amount": "10.25", "currencyCode": "USD"},
                        },
                        "userErrors": [],
                    }
                }
            },
            {
                "data": {
                    "node": {
                        "__typename": "AppUsageRecord",
                        "id": "gid://shopify/AppUsageRecord/55",
                        "description": "Test charge",
                        "price": {"amount": "10.25", "currencyCode": "USD"},
                        "createdAt": "2025-01-01T00:00:00Z",
                    }
                }
            },
        ]

        details = billing.create_usage_charge(
            self.meta,
            amount=Decimal("10.25"),
            description="Test charge",
        )

        mock_client.graphql.assert_called()
        self.assertEqual(details.charge_id, "55")
        self.assertEqual(details.amount, Decimal("10.25"))

    @patch("shopify_app.billing.ShopifyClient")
    def test_refresh_recurring_charge_requires_subscription_node(self, mock_client_cls):
        self.meta.shopify_recurring_charge_id = "123"
        self.meta.save()

        mock_client = mock_client_cls.return_value
        mock_client.graphql.return_value = {"data": {"node": {"__typename": "Shop"}}}

        with self.assertRaises(billing.ShopifyBillingError) as context:
            billing.refresh_recurring_charge(self.meta)

        self.assertIn("Admin API endpoint", str(context.exception))


@override_settings(SHOPIFY_API_KEY="key")
class ShopifyBillingReturnTests(TestCase):
    def setUp(self):
        self.url = reverse("shopify_billing_return")

    def test_missing_shop_returns_error(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing shop identifier", response.content.decode())

    def test_active_charge_success_redirects(self):
        user = CustomUser.objects.create_user(
            username="merchant", email="merchant@example.com", password="pass"
        )
        meta = MerchantMeta.objects.create(
            user=user,
            shopify_store_domain="example.myshopify.com",
            shopify_access_token="token",
            monthly_fee=Decimal("10.00"),
            shopify_recurring_charge_id="123",
            shopify_billing_status="active",
        )

        response = self.client.get(self.url, {"shop": meta.shopify_store_domain})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"https://{meta.shopify_store_domain}/admin/apps/key",
        )


class MerchantInvoiceAdminTests(TestCase):
    def setUp(self):
        self.staff = CustomUser.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        self.client.force_login(self.staff)

    @patch("ledger.admin.generate_all_invoices")
    def test_generate_all_triggers_message(self, mock_generate):
        result = MagicMock()
        result.pending_shopify = []
        result.__len__.return_value = 2
        mock_generate.return_value = result

        url = reverse("admin:ledger_invoice_generate_all")
        response = self.client.post(url, follow=True)

        mock_generate.assert_called_once_with(ignore_date=True)

        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(
            any("Generated 2 invoice(s) or Shopify charges" in message for message in messages)
        )


class ShopifyClientRefreshTests(SimpleTestCase):
    @patch("shopify_app.shopify_client.requests.request")
    def test_request_refreshes_token_on_unauthorized(self, mock_request):
        first_response = MagicMock(status_code=401)
        first_response.raise_for_status.side_effect = HTTPError(response=first_response)
        second_response = MagicMock(status_code=200)
        second_response.raise_for_status.return_value = None
        mock_request.side_effect = [first_response, second_response]

        refresh = MagicMock(return_value="new_token")
        client = ShopifyClient(
            "old_token",
            "example.myshopify.com",
            refresh_handler=refresh,
        )

        response = client.request("GET", "/admin/api/2024-07/shop.json")

        self.assertIs(response, second_response)
        self.assertEqual(mock_request.call_count, 2)
        refresh.assert_called_once_with()
        first_headers = mock_request.call_args_list[0].kwargs["headers"]
        second_headers = mock_request.call_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["X-Shopify-Access-Token"], "old_token")
        self.assertEqual(second_headers["X-Shopify-Access-Token"], "new_token")

    @patch("shopify_app.shopify_client.requests.request")
    def test_request_raises_when_refresh_fails(self, mock_request):
        response = MagicMock(status_code=401)
        error = HTTPError(response=response)
        response.raise_for_status.side_effect = error
        response.text = ""
        mock_request.return_value = response

        refresh = MagicMock(return_value=None)
        client = ShopifyClient(
            "expired",
            "example.myshopify.com",
            refresh_handler=refresh,
        )

        with self.assertRaises(HTTPError):
            client.request("GET", "/admin/api/2024-07/shop.json")

        refresh.assert_called_once_with()
        mock_request.assert_called_once()

    @patch("shopify_app.shopify_client.requests.request")
    def test_request_raises_invalid_credentials_error(self, mock_request):
        response = MagicMock(status_code=401)
        response.raise_for_status.side_effect = HTTPError(response=response)
        response.text = "Invalid API key or access token"
        mock_request.return_value = response

        client = ShopifyClient("token", "example.myshopify.com")

        with self.assertRaises(ShopifyInvalidCredentialsError):
            client.request("GET", "/admin/api/2024-07/shop.json")


class ShopifyClientGraphQLTests(SimpleTestCase):
    def test_graphql_uses_admin_endpoint(self):
        client = ShopifyClient("token", "example.myshopify.com")
        response = MagicMock()
        response.json.return_value = {"data": {}}
        client.post = MagicMock(return_value=response)

        client.graphql("query { shop { name } }")

        path = client.post.call_args[0][0]
        self.assertIn("/admin/api/", path)
        self.assertIn(ADMIN_API_VERSION, path)


class ShopifyClientProductsTests(SimpleTestCase):
    def test_get_all_products_parses_money_values(self):
        client = ShopifyClient("token", "example.myshopify.com")

        client.graphql = MagicMock(
            return_value={
                "data": {
                    "products": {
                        "edges": [
                            {
                                "node": {
                                    "id": "gid://shopify/Product/123",
                                    "title": "Snowboard",
                                    "status": "ACTIVE",
                                    "handle": "snowboard",
                                    "onlineStoreUrl": "https://example.myshopify.com/products/snowboard",
                                    "variants": {
                                        "edges": [
                                            {
                                                "node": {
                                                    "id": "gid://shopify/ProductVariant/345",
                                                    "title": "Default",
                                                    "price": "125.00",
                                                }
                                            }
                                        ]
                                    },
                                    "images": {
                                        "edges": [
                                            {
                                                "node": {
                                                    "originalSrc": "https://example.myshopify.com/img.jpg",
                                                }
                                            }
                                        ]
                                    },
                                }
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        )

        products = client.get_all_products()

        self.assertEqual(
            products,
            [
                {
                    "id": "123",
                    "title": "Snowboard",
                    "status": "ACTIVE",
                    "handle": "snowboard",
                    "onlineStoreUrl": "https://example.myshopify.com/products/snowboard",
                    "productType": None,
                    "featuredImage": {"src": None},
                    "variants": [
                        {"id": "345", "title": "Default", "price": "125.00"}
                    ],
                    "images": [{"src": "https://example.myshopify.com/img.jpg"}],
                }
            ],
        )
        client.graphql.assert_called_with(_PRODUCTS_QUERY, {"cursor": None, "pageSize": 50})


class ShopifyTokenManagementTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="token-user",
            email="token@example.com",
            password="pass12345",
        )

    @patch("shopify_app.token_management.refresh_access_token")
    def test_refresh_updates_tokens(self, mock_refresh):
        meta = MerchantMeta.objects.create(
            user=self.user,
            shopify_store_domain="example.myshopify.com",
            shopify_access_token="old",
            shopify_refresh_token="refresh_old",
        )

        mock_refresh.return_value = AccessTokenResponse(
            access_token="new",
            scope="read_products",
            associated_user_scope="",
            refresh_token="refresh_new",
            raw={},
        )

        new_token = refresh_shopify_token(meta)

        self.assertEqual(new_token, "new")
        meta.refresh_from_db()
        self.assertEqual(meta.shopify_access_token, "new")
        self.assertEqual(meta.shopify_refresh_token, "refresh_new")
        mock_refresh.assert_called_once()

    @patch("shopify_app.token_management.refresh_access_token")
    def test_refresh_failure_clears_access_token(self, mock_refresh):
        mock_refresh.side_effect = ShopifyOAuthError("invalid")

        meta = MerchantMeta.objects.create(
            user=self.user,
            shopify_store_domain="example.myshopify.com",
            shopify_access_token="old",
            shopify_refresh_token="refresh_old",
        )

        result = refresh_shopify_token(meta)

        self.assertIsNone(result)
        meta.refresh_from_db()
        self.assertEqual(meta.shopify_access_token, "")
        self.assertEqual(meta.shopify_refresh_token, "")

    def test_clear_shopify_token_removes_tokens(self):
        meta = MerchantMeta.objects.create(
            user=self.user,
            shopify_store_domain="example.myshopify.com",
            shopify_access_token="token",
            shopify_refresh_token="refresh",
        )

        result = clear_shopify_token_for_shop("example.myshopify.com")

        self.assertEqual(result.pk, meta.pk)
        meta.refresh_from_db()
        self.assertEqual(meta.shopify_access_token, "")
        self.assertEqual(meta.shopify_refresh_token, "")


@override_settings(SHOPIFY_UNINSTALL_GRACE_DAYS=30)
class ShopifyUninstallLifecycleTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="merchant_lifecycle",
            password="pass12345",
            email="merchant_lifecycle@example.com",
            is_merchant=True,
        )
        self.meta = self.user.merchantmeta
        self.meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        self.meta.shopify_store_domain = "example.myshopify.com"
        self.meta.shopify_access_token = "token"
        self.meta.shopify_refresh_token = "refresh"
        self.meta.shopify_billing_status = "ACTIVE"
        self.meta.shopify_billing_plan = MerchantMeta.BillingPlan.BADGER_CREATOR
        self.meta.save(
            update_fields=[
                "business_type",
                "shopify_store_domain",
                "shopify_access_token",
                "shopify_refresh_token",
                "shopify_billing_status",
                "shopify_billing_plan",
            ]
        )

    @patch("shopify_app.uninstall.views.is_valid_shopify_webhook", return_value=True)
    def test_uninstall_webhook_keeps_account_active_during_grace_period(self, _mock_signature):
        response = self.client.post(
            reverse("shopify_webhooks_app_uninstalled"),
            data='{"myshopify_domain": "example.myshopify.com"}',
            content_type="application/json",
            HTTP_X_SHOPIFY_SHOP_DOMAIN="example.myshopify.com",
        )

        self.assertEqual(response.status_code, 200)
        self.meta.refresh_from_db()
        self.user.refresh_from_db()

        self.assertIsNotNone(self.meta.shopify_uninstalled_at)
        self.assertEqual(self.meta.shopify_billing_status, "CANCELLED")
        self.assertEqual(self.meta.shopify_access_token, "")
        self.assertEqual(self.meta.shopify_refresh_token, "")
        self.assertTrue(self.user.is_active)

    def test_expired_grace_releases_shop_association_and_deactivates_account(self):
        self.meta.cancel_shopify_account(
            canceled_at=timezone.now() - timezone.timedelta(days=31)
        )

        self.assertTrue(self.meta.process_shopify_uninstall_grace_expiration())

        self.meta.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.meta.shopify_store_domain, "")
        self.assertEqual(self.meta.shopify_oauth_authorization_line, "")
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.is_merchant)

    def test_management_command_releases_expired_uninstalls(self):
        self.meta.cancel_shopify_account(
            canceled_at=timezone.now() - timezone.timedelta(days=31)
        )

        call_command("process_shopify_uninstall_grace")

        self.meta.refresh_from_db()
        self.assertEqual(self.meta.shopify_store_domain, "")


@override_settings(SHOPIFY_API_SECRET="shh", SHOPIFY_API_KEY="key", SHOPIFY_UNINSTALL_GRACE_DAYS=30)
class ShopifyReinstallAfterGraceTests(TestCase):
    def setUp(self):
        self.shop_domain = "example.myshopify.com"
        self.url = reverse("shopify_oauth_callback")

    @patch("shopify_app.oauth.exchange_code_for_token")
    @patch("shopify_app.oauth.validate_shopify_hmac", return_value=True)
    @patch("shopify_app.views._attempt_script_tag_injection", return_value=None)
    @patch("shopify_app.views._attempt_shopify_webhook_registration", return_value=False)
    def test_new_account_can_claim_store_after_old_account_grace_expired(
        self, _mock_webhooks, _mock_scripts, _mock_hmac, mock_exchange
    ):
        old_user = CustomUser.objects.create_user(
            username="old_merchant",
            email="old@example.com",
            password="pass12345",
            is_merchant=True,
        )
        old_meta = old_user.merchantmeta
        old_meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        old_meta.shopify_store_domain = self.shop_domain
        old_meta.shopify_access_token = "old_token"
        old_meta.shopify_refresh_token = "old_refresh"
        old_meta.save(
            update_fields=[
                "business_type",
                "shopify_store_domain",
                "shopify_access_token",
                "shopify_refresh_token",
            ]
        )
        old_meta.cancel_shopify_account(
            canceled_at=timezone.now() - timezone.timedelta(days=31)
        )

        new_user = CustomUser.objects.create_user(
            username="new_merchant",
            email="new@example.com",
            password="pass12345",
        )
        new_meta, _ = MerchantMeta.objects.get_or_create(user=new_user)
        new_meta.business_type = MerchantMeta.BusinessType.INDEPENDENT
        new_meta.save(update_fields=["business_type"])
        self.client.force_login(new_user)

        mock_exchange.return_value = AccessTokenResponse(
            access_token="new_offline_token",
            scope="read_products",
            associated_user_scope="",
            refresh_token="new_refresh_token",
            raw={},
        )

        session = self.client.session
        session[STATE_SESSION_KEY] = "abc"
        session.save()

        response = self.client.get(
            self.url,
            {"shop": self.shop_domain, "code": "abc", "state": "abc", "hmac": "1"},
        )

        self.assertEqual(response.status_code, 200)

        old_meta.refresh_from_db()
        new_meta.refresh_from_db()
        old_user.refresh_from_db()

        self.assertEqual(old_meta.shopify_store_domain, "")
        self.assertFalse(old_user.is_active)
        self.assertEqual(new_meta.shopify_store_domain, self.shop_domain)
        self.assertEqual(new_meta.shopify_access_token, "new_offline_token")
        self.assertEqual(new_meta.business_type, MerchantMeta.BusinessType.SHOPIFY)

@override_settings(SHOPIFY_API_SECRET="shh", SHOPIFY_API_KEY="key")
class ShopifyOAuthAuthorizeTests(TestCase):
    def test_uses_configured_redirect_and_scopes(self):
        configured_redirect = "https://app.example.com/shopify/callback/"
        with override_settings(
            SHOPIFY_REDIRECT_URI=configured_redirect,
            SHOPIFY_SCOPES=["read_products", "write_discounts"],
        ):
            response = self.client.get(
                reverse("shopify_oauth_authorize"),
                {"shop": "example.myshopify.com"},
            )

        self.assertEqual(response.status_code, 200)
        response.render()
        redirect_target = response.context_data["redirect_url"]
        redirect_url = urlparse(redirect_target)
        params = parse_qs(redirect_url.query)

        self.assertEqual(params["redirect_uri"], [configured_redirect])
        self.assertEqual(params["scope"], ["read_products,write_discounts"])

        session = self.client.session
        self.assertEqual(session[CALLBACK_SESSION_KEY], configured_redirect)

    def test_fallback_redirect_upgrades_to_https(self):
        response = self.client.get(
            reverse("shopify_oauth_authorize"),
            {"shop": "example.myshopify.com"},
            secure=False,
        )

        self.assertEqual(response.status_code, 200)
        response.render()
        redirect_url = urlparse(response.context_data["redirect_url"])
        params = parse_qs(redirect_url.query)

        self.assertTrue(params["redirect_uri"][0].startswith("https://"))

    def test_response_contains_top_window_redirect_script(self):
        response = self.client.get(
            reverse("shopify_oauth_authorize"),
            {"shop": "example.myshopify.com"},
        )

        self.assertEqual(response.status_code, 200)
        response.render()
        content = response.content.decode()

        self.assertIn("window.top", content)
        self.assertIn("Continue to Shopify", content)


class ShopifyExchangeCodeTests(TestCase):
    @override_settings(SHOPIFY_API_SECRET="secret", SHOPIFY_API_KEY="key")
    @patch("shopify_app.oauth.requests.post")
    def test_exchange_code_includes_redirect_uri(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "token",
            "scope": "read_products",
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        token_response = exchange_code_for_token(
            "example.myshopify.com",
            "code123",
            redirect_uri="https://app.example.com/shopify/callback/",
        )

        self.assertEqual(token_response.access_token, "token")
        self.assertTrue(mock_post.called)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(
            payload["redirect_uri"], "https://app.example.com/shopify/callback/"
        )


@override_settings(SHOPIFY_API_SECRET="shh", SHOPIFY_API_KEY="key")
class EmbeddedAppHomeTests(TestCase):
    def setUp(self):
        self.shop_domain = "example.myshopify.com"
        self.access_token = "shpua_token"
        self.billing_patcher = patch(
            "shopify_app.views.billing.create_or_update_recurring_charge",
            return_value={},
        )
        self.billing_patcher.start()

    def _store_session_token(self):
        session = self.client.session
        session[session_token_key(self.shop_domain)] = self.access_token
        session.save()

    def tearDown(self):
        self.billing_patcher.stop()

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
        self.assertContains(response, reverse("password_reset"))
        self.assertContains(response, "Forgot your password?")

    def test_get_without_stored_token_restarts_oauth(self):
        url = reverse("shopify_embedded_home")

        response = self.client.get(url, self._signed_params())

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"https://{self.shop_domain}/admin/oauth/authorize", response["Location"])
        self.assertIn("client_id=key", response["Location"])
        self.assertEqual(
            self.client.session.get(views.PENDING_ONBOARD_SESSION_KEY), self.shop_domain
        )

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
        self.assertNotIn(session_token_key(self.shop_domain), self.client.session)
        self.assertNotIn(session_scope_key(self.shop_domain), self.client.session)

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
        self.assertNotIn(session_scope_key(self.shop_domain), self.client.session)

    def test_post_login_recovers_shopify_context_from_signed_query(self):
        user = CustomUser.objects.create_user(
            username="merchant2",
            email="merchant2@example.com",
            password="pass12345",
        )

        MerchantMeta.objects.create(
            user=user,
            shopify_store_domain=self.shop_domain,
            shopify_access_token=self.access_token,
        )

        url = reverse("shopify_embedded_home")
        response = self.client.post(
            f"{url}?{urlencode(self._signed_params())}",
            {
                "action": "login",
                "username": "merchant2@example.com",
                "password": "pass12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("merchant_dashboard"))
        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)

    def test_login_with_invalid_credentials_rerenders_with_success_status(self):
        user = CustomUser.objects.create_user(
            username="merchant3",
            email="merchant3@example.com",
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
                "username": "merchant3@example.com",
                "password": "wrong-password",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password")


@override_settings(SHOPIFY_API_SECRET="shh", SHOPIFY_API_KEY="key")
class OAuthCallbackTests(TestCase):
    def setUp(self):
        self.url = reverse("shopify_oauth_callback")
        self.shop_domain = "example.myshopify.com"
        self.access_token = "shppa_token"
        self.billing_patcher = patch(
            "shopify_app.views.billing.create_or_update_recurring_charge",
            return_value={},
        )
        self.billing_patcher.start()

    def tearDown(self):
        self.billing_patcher.stop()

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

    def test_session_token_unknown_store_starts_oauth(self):
        response = self.client.get(self.url, {"id_token": self._build_id_token()})

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(
            location.startswith(
                f"https://{self.shop_domain}/admin/oauth/authorize?"
            )
        )
        self.assertIn("client_id=key", location)
        self.assertIn("state=", location)
        self.assertEqual(
            self.client.session.get("shopify_pending_shop"), self.shop_domain
        )

    @override_settings(SHOPIFY_API_SECRET="")
    def test_session_token_error_with_shop_falls_back_to_oauth(self):
        response = self.client.get(self.url, {"id_token": self._build_id_token()})

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(
            location.startswith(
                f"https://{self.shop_domain}/admin/oauth/authorize?"
            )
        )
        self.assertIn("client_id=key", location)
        self.assertIn("state=", location)
        self.assertEqual(
            self.client.session.get("shopify_pending_shop"), self.shop_domain
        )

    @patch("shopify_app.oauth.exchange_code_for_token")
    @patch("shopify_app.oauth.validate_shopify_hmac", return_value=True)
    def test_oauth_callback_stores_session_token(self, mock_hmac, mock_exchange):
        mock_exchange.return_value = AccessTokenResponse(
            access_token="shppa_token",
            scope="read_products",
            associated_user_scope="",
            refresh_token="refresh_abc",
            raw={},
        )

        session = self.client.session
        session[STATE_SESSION_KEY] = "abc"
        session.save()

        response = self.client.get(
            self.url,
            {"shop": self.shop_domain, "code": "abc", "state": "abc", "hmac": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(session_token_key(self.shop_domain), self.client.session)
        self.assertIn(session_scope_key(self.shop_domain), self.client.session)
        self.assertEqual(
            self.client.session.get(session_refresh_key(self.shop_domain)),
            "refresh_abc",
        )
        content = response.content.decode()
        self.assertIn("window.top.location.href", content)
        self.assertIn(
            "https://example.myshopify.com/admin/apps/key",
            content,
        )

    @patch("shopify_app.oauth.exchange_code_for_token")
    @patch("shopify_app.oauth.validate_shopify_hmac", return_value=True)
    def test_oauth_callback_links_authenticated_user(self, mock_hmac, mock_exchange):
        user = CustomUser.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="pass12345",
        )
        meta = MerchantMeta.objects.create(
            user=user,
            company_name="Acme",
            business_type=MerchantMeta.BusinessType.INDEPENDENT,
        )

        self.client.force_login(user)

        mock_exchange.return_value = AccessTokenResponse(
            access_token="shppa_token",
            scope="read_products,write_products",
            associated_user_scope="",
            refresh_token="refresh_xyz",
            raw={},
        )

        session = self.client.session
        session[STATE_SESSION_KEY] = "abc"
        session.save()

        response = self.client.get(
            self.url,
            {"shop": self.shop_domain, "code": "abc", "state": "abc", "hmac": "1"},
        )

        self.assertEqual(response.status_code, 200)

        meta.refresh_from_db()
        self.assertEqual(meta.shopify_store_domain, self.shop_domain)
        self.assertEqual(meta.shopify_access_token, "shppa_token")
        self.assertEqual(meta.shopify_refresh_token, "refresh_xyz")
        self.assertEqual(meta.business_type, MerchantMeta.BusinessType.SHOPIFY)
        self.assertIn("connected_at=", meta.shopify_oauth_authorization_line)

    @patch("shopify_app.oauth.exchange_code_for_token")
    @patch("shopify_app.oauth.validate_shopify_hmac", return_value=True)
    def test_oauth_callback_overwrites_existing_token(self, mock_hmac, mock_exchange):
        user = CustomUser.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="pass12345",
        )
        meta = MerchantMeta.objects.create(
            user=user,
            shopify_store_domain=self.shop_domain,
            shopify_access_token="old_token",
            shopify_refresh_token="old_refresh",
            business_type=MerchantMeta.BusinessType.SHOPIFY,
        )

        self.client.force_login(user)

        mock_exchange.return_value = AccessTokenResponse(
            access_token="new_offline_token",
            scope="read_products",
            associated_user_scope="",
            refresh_token="new_refresh_token",
            raw={},
        )

        session = self.client.session
        session[STATE_SESSION_KEY] = "abc"
        session.save()

        response = self.client.get(
            self.url,
            {"shop": self.shop_domain, "code": "abc", "state": "abc", "hmac": "1"},
        )

        self.assertEqual(response.status_code, 200)

        meta.refresh_from_db()
        self.assertEqual(meta.shopify_access_token, "new_offline_token")
        self.assertEqual(meta.shopify_refresh_token, "new_refresh_token")

    def test_invalid_session_token_triggers_reauthorize(self):
        bad_token = jwt.encode({"iss": "bad"}, "wrong", algorithm="HS256")
        if isinstance(bad_token, bytes):
            bad_token = bad_token.decode("utf-8")

        session = self.client.session
        session[session_token_key(self.shop_domain)] = "cached_token"
        session.save()

        response = self.client.get(
            self.url,
            {"id_token": bad_token, "shop": self.shop_domain},
        )

        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(
            location.startswith(
                f"https://{self.shop_domain}/admin/oauth/authorize?"
            )
        )
        self.assertIn("client_id=key", location)
        self.assertIn("state=", location)
        self.assertNotIn(session_token_key(self.shop_domain), self.client.session)

    def test_invalid_session_token_retries_are_throttled(self):
        bad_token = jwt.encode({"iss": "bad"}, "wrong", algorithm="HS256")
        if isinstance(bad_token, bytes):
            bad_token = bad_token.decode("utf-8")

        retry_key = "shopify_session_retry:example.myshopify.com"
        session = self.client.session
        session[retry_key] = str(timezone.now().timestamp())
        session.save()

        response = self.client.get(
            self.url,
            {"id_token": bad_token, "shop": self.shop_domain},
        )

        self.assertEqual(response.status_code, 400)
        html_response = html.unescape(response.content.decode())
        self.assertIn(
            "We couldn't validate your Shopify session. Please try again in a moment.",
            html_response,
        )
