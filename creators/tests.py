from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import CustomUser
from .models import CreatorMeta
from ledger.models import LedgerEntry
from collect.models import ReferralVisit, ReferralConversion
from links.models import (
    MerchantCreatorLink,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_REQUESTED,
)
from merchants.models import MerchantMeta, ItemGroup, MerchantItem
from rest_framework_simplejwt.tokens import RefreshToken
from .services.social_dashboard import InstagramAnalyticsService


class CreatorProfileTests(TestCase):
    def test_profile_displays_uuid(self):
        user = CustomUser.objects.create_user(
            username="creator_uuid",
            password="pass",
            email="creator_uuid@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_profile"))
        creator_meta = CreatorMeta.objects.get(user=user)
        self.assertContains(response, str(creator_meta.uuid))

    def test_profile_displays_email(self):
        user = CustomUser.objects.create_user(
            username="creator",
            password="pass",
            email="creator@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_profile"))
        self.assertContains(response, user.email)

    def test_profile_updates_name(self):
        user = CustomUser.objects.create_user(
            username="creator3",
            password="pass123",
            email="creator3@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("creator_profile"),
            {
                "first_name": "New",
                "last_name": "Name",
                "email": "creator3@example.com",
            },
        )
        self.assertRedirects(response, reverse("creator_profile"))
        user.refresh_from_db()
        self.assertEqual(user.last_name, "Name")


class CreatorNameAPITests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="creator_api",
            password="pass123",
            email="creator_api@example.com",
            first_name="Api",
            last_name="Tester",
            is_creator=True,
        )
        self.meta = CreatorMeta.objects.get(user=self.user)
        self.token = str(RefreshToken.for_user(self.user).access_token)

    def test_requires_authentication(self):
        url = reverse("creator_name_api", kwargs={"uuid": self.meta.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

    def test_returns_creator_name(self):
        url = reverse("creator_name_api", kwargs={"uuid": self.meta.uuid})
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("name"), "Api Tester")


class CreatorRequestTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="creator_req",
            password="pass",
            email="creator_req@example.com",
            is_creator=True,
        )
        self.merchant = CustomUser.objects.create_user(
            username="merchant_req",
            password="pass",
            email="merchant_req@example.com",
            is_merchant=True,
        )

    def test_pending_request_displayed(self):
        MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_REQUESTED,
        )
        self.client.force_login(self.creator)
        response = self.client.get(reverse("creator_affiliate_companies"))
        self.assertContains(response, self.merchant.username)
        self.assertContains(response, "Accept")

    def test_accept_request(self):
        link = MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_REQUESTED,
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("respond_request", args=[link.id]), {"action": "accept"}
        )
        self.assertRedirects(response, reverse("creator_affiliate_companies"))
        link.refresh_from_db()
        self.assertEqual(link.status, STATUS_ACTIVE)

    def test_decline_request(self):
        link = MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_REQUESTED,
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("respond_request", args=[link.id]), {"action": "decline"}
        )
        self.assertRedirects(response, reverse("creator_affiliate_companies"))
        self.assertFalse(
            MerchantCreatorLink.objects.filter(id=link.id).exists()
        )


class CreatorAffiliateCompaniesViewTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="creator_aff_view",
            password="pass",
            email="creator_aff_view@example.com",
            is_creator=True,
        )
        self.merchant = CustomUser.objects.create_user(
            username="merchant_aff_view",
            password="pass",
            email="merchant_aff_view@example.com",
            is_merchant=True,
        )
        self.merchant_meta = MerchantMeta.objects.get(user=self.merchant)
        self.merchant_meta.company_name = "Merchant Aff LLC"
        self.merchant_meta.save()
        self.link = MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_ACTIVE,
        )
        self.client.force_login(self.creator)

    def test_displays_company_metrics(self):
        creator_meta = CreatorMeta.objects.get(user=self.creator)
        ReferralVisit.objects.create(
            creator_uuid=creator_meta.uuid,
            merchant_uuid=self.merchant_meta.uuid,
            creator=self.creator,
            merchant=self.merchant,
        )
        ReferralConversion.objects.create(
            creator_uuid=creator_meta.uuid,
            merchant_uuid=self.merchant_meta.uuid,
            creator=self.creator,
            merchant=self.merchant,
            order_id="order-1",
            order_amount=Decimal("200.00"),
            commission_amount=Decimal("42.50"),
        )
        LedgerEntry.objects.create(
            creator=self.creator,
            merchant=self.merchant,
            amount=Decimal("42.50"),
            entry_type="commission",
        )

        html_response = self.client.get(reverse("creator_affiliate_companies"))
        self.assertContains(html_response, "affiliate-companies-root")

        data_response = self.client.get(reverse("creator_affiliate_companies_data"))
        self.assertEqual(data_response.status_code, 200)
        payload = data_response.json()
        self.assertEqual(len(payload["active"]), 1)
        company = payload["active"][0]
        self.assertEqual(company["business"], "Merchant Aff LLC")
        self.assertAlmostEqual(company["total_earnings"], 42.5)
        self.assertAlmostEqual(company["monthly_earnings"], 42.5)
        self.assertEqual(company["visits"], 1)
        self.assertEqual(company["conversions"], 1)
        self.assertAlmostEqual(company["avg_per_visit"], 42.5)
        self.assertAlmostEqual(company["conversion_rate"], 100.0)

    def test_inactive_company_lists_under_inactive_tab(self):
        self.link.status = STATUS_INACTIVE
        self.link.save()
        creator_meta = CreatorMeta.objects.get(user=self.creator)
        ReferralVisit.objects.create(
            creator_uuid=creator_meta.uuid,
            merchant_uuid=self.merchant_meta.uuid,
            creator=self.creator,
            merchant=self.merchant,
        )
        ReferralConversion.objects.create(
            creator_uuid=creator_meta.uuid,
            merchant_uuid=self.merchant_meta.uuid,
            creator=self.creator,
            merchant=self.merchant,
        )

        payload = self.client.get(reverse("creator_affiliate_companies_data")).json()
        self.assertEqual(len(payload["active"]), 0)
        self.assertEqual(len(payload["inactive"]), 1)
        self.assertEqual(payload["inactive"][0]["business"], "Merchant Aff LLC")

    def test_delete_affiliate_company(self):
        response = self.client.post(
            reverse("creator_delete_affiliations"),
            {"selected_links": [str(self.link.id)]},
        )
        self.assertRedirects(response, reverse("creator_affiliate_companies"))
        self.assertFalse(
            MerchantCreatorLink.objects.filter(id=self.link.id).exists()
        )


class CreatorLinksTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="creator_links",
            password="pass",
            email="creator_links@example.com",
            is_creator=True,
        )
        self.creator_meta = CreatorMeta.objects.get(user=self.creator)
        self.merchant = CustomUser.objects.create_user(
            username="merchant_links",
            password="pass",
            email="merchant_links@example.com",
            is_merchant=True,
        )
        self.merchant_meta = MerchantMeta.objects.get(user=self.merchant)
        self.merchant_meta.company_name = "Nike"
        self.merchant_meta.save()
        self.group = ItemGroup.objects.create(
            merchant=self.merchant, name="Group 1", affiliate_percent=10
        )
        self.item = MerchantItem.objects.create(
            merchant=self.merchant,
            title="Shoe",
            link="https://example.com/shoe",
            shopify_product_id="222",
        )
        self.group.items.add(self.item)
        MerchantCreatorLink.objects.create(
            merchant=self.merchant, creator=self.creator, status=STATUS_ACTIVE
        )
        self.client.force_login(self.creator)

    def test_company_list(self):
        response = self.client.get(reverse("creator_my_links"))
        self.assertContains(response, "Nike")

    def test_group_list(self):
        url = reverse("creator_my_links_merchant", args=[self.merchant.id])
        response = self.client.get(url)
        self.assertContains(response, "Group 1")
        self.assertContains(response, "10")

    def test_item_list_includes_affiliate_link(self):
        url = reverse(
            "creator_my_links_group", args=[self.merchant.id, self.group.id]
        )
        response = self.client.get(url)
        expected_link = (
            f"{self.item.link}?ref=badger:{self.creator_meta.uuid}&item_id={self.item.shopify_product_id}"
        )
        self.assertContains(response, expected_link.replace("&", "&amp;"))

    def test_search_items_by_name_and_id(self):
        other = MerchantItem.objects.create(
            merchant=self.merchant,
            title="Hat",
            link="https://example.com/hat",
            shopify_product_id="333",
        )
        self.group.items.add(other)
        url = reverse("creator_my_links_group", args=[self.merchant.id, self.group.id])
        response = self.client.get(url, {"q": "Hat"})
        self.assertContains(response, "Hat")
        self.assertNotContains(response, "Shoe")
        response = self.client.get(url, {"q": other.shopify_product_id})
        self.assertContains(response, "Hat")
        self.assertNotContains(response, "Shoe")


class InstagramAnalyticsServiceTests(SimpleTestCase):
    @patch.object(InstagramAnalyticsService, "_safe_json_get")
    def test_fetch_recent_media_handles_null_data(self, mock_safe_json_get):
        service = InstagramAnalyticsService(user=None)
        connection = SimpleNamespace(instagram_access_token="token_123")
        mock_safe_json_get.return_value = {"data": None}

        media = service.fetch_recent_media(connection, "123456789")

        self.assertEqual(media, [])

    @patch.object(InstagramAnalyticsService, "_safe_json_get")
    def test_media_insights_use_graph_instagram_and_supported_metrics(self, mock_safe_json_get):
        service = InstagramAnalyticsService(user=None)
        connection = SimpleNamespace(instagram_access_token="token_123")
        media = [{"id": "m1", "media_type": "IMAGE", "media_product_type": "FEED"}]
        calls: list[tuple[str, dict]] = []

        def _record(url, params):
            calls.append((url, params))
            return {"data": [{"name": params.get("metric"), "period": "lifetime", "values": [{"value": 1}]}]}

        mock_safe_json_get.side_effect = _record
        insights = service.fetch_media_insights(connection, media)

        self.assertTrue(insights)
        for url, params in calls:
            self.assertEqual(urlparse(url).netloc, "graph.instagram.com")
            self.assertNotEqual(urlparse(url).netloc, "graph.facebook.com")
            self.assertNotIn(params.get("metric"), {"impressions", "plays", "video_views"})
            self.assertNotIn(params.get("metric"), {"total_comments", "total_likes", "total_views"})
        requested_metrics = {params.get("metric") for _, params in calls}
        self.assertIn("views", requested_metrics)
        self.assertNotIn("impressions", requested_metrics)

    @patch.object(InstagramAnalyticsService, "_safe_json_get")
    def test_breakdown_metrics_requested_separately(self, mock_safe_json_get):
        service = InstagramAnalyticsService(user=None)
        connection = SimpleNamespace(instagram_access_token="token_123")
        media = [{"id": "story1", "media_type": "STORY", "media_product_type": "STORY"}]
        calls: list[tuple[str, dict]] = []

        def _record(url, params):
            calls.append((url, params))
            return {"data": []}

        mock_safe_json_get.side_effect = _record
        service.fetch_media_insights(connection, media)

        self.assertTrue(any(params.get("metric") == "navigation" for _, params in calls))
        self.assertTrue(
            any(
                params.get("metric") == "navigation"
                and params.get("breakdown") == "story_navigation_action_type"
                for _, params in calls
            )
        )
        self.assertTrue(
            any(
                params.get("metric") == "profile_activity" and params.get("breakdown") == "action_type"
                for _, params in calls
            )
        )

    @patch("creators.services.social_dashboard.requests.get")
    def test_story_error_code_10_and_empty_data_do_not_crash(self, mock_get):
        response = SimpleNamespace(
            status_code=400,
            json=lambda: {"error": {"code": 10, "message": "Not available for this story"}},
        )
        mock_get.return_value = response
        service = InstagramAnalyticsService(user=None)

        payload = service._safe_json_get(
            "https://graph.instagram.com/v25.0/story123/insights",
            {"metric": "views", "access_token": "token_123"},
        )

        self.assertEqual(payload, {})
        self.assertEqual(service.failed_requests, [])

    @patch.object(InstagramAnalyticsService, "fetch_single_account_metric")
    def test_account_performance_requests_only_account_level_metrics(self, mock_fetch_single_account_metric):
        mock_fetch_single_account_metric.return_value = 1
        service = InstagramAnalyticsService(user=None)
        connection = SimpleNamespace(instagram_access_token="token_123")

        performance = service.fetch_account_performance(connection, "1789")

        requested = [call.args[2] for call in mock_fetch_single_account_metric.call_args_list]
        self.assertEqual(
            requested,
            [
                "reach",
                "follower_count",
                "online_followers",
                "profile_views",
                "website_clicks",
                "accounts_engaged",
                "total_interactions",
                "views",
                "follows_and_unfollows",
                "profile_links_taps",
            ],
        )
        self.assertNotIn("likes", performance)
        self.assertNotIn("comments", performance)
        self.assertNotIn("shares", performance)
        self.assertNotIn("saved", performance)

    @patch.object(InstagramAnalyticsService, "_safe_json_get")
    def test_feed_media_metrics_do_not_request_reposts(self, mock_safe_json_get):
        service = InstagramAnalyticsService(user=None)
        connection = SimpleNamespace(instagram_access_token="token_123")
        media = [{"id": "feed1", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED"}]
        calls: list[tuple[str, dict]] = []

        def _record(url, params):
            calls.append((url, params))
            return {"data": []}

        mock_safe_json_get.side_effect = _record
        service.fetch_media_insights(connection, media)

        requested = {params.get("metric") for _, params in calls}
        self.assertNotIn("reposts", requested)

    @patch("creators.services.social_dashboard.requests.get")
    def test_empty_data_payload_is_not_marked_as_failed_request(self, mock_get):
        response = SimpleNamespace(status_code=200, json=lambda: {"data": []})
        mock_get.return_value = response
        service = InstagramAnalyticsService(user=None)

        payload = service._safe_json_get(
            "https://graph.instagram.com/v25.0/123/insights",
            {"metric": "reach", "access_token": "token_123"},
        )

        self.assertEqual(payload, {"data": []})
        self.assertEqual(service.failed_requests, [])

    @patch.object(InstagramAnalyticsService, "_safe_json_get")
    def test_fetch_demographics_calls_follower_demographics_breakdowns(self, mock_safe_json_get):
        service = InstagramAnalyticsService(user=None)
        connection = SimpleNamespace(instagram_access_token="token_123")
        calls: list[tuple[str, dict]] = []

        def _record(url, params):
            calls.append((url, params))
            if params.get("breakdown") == "country":
                return {
                    "data": [
                        {
                            "name": "follower_demographics",
                            "total_value": {
                                "breakdowns": [
                                    {
                                        "results": [
                                            {"dimension_values": ["US"], "value": 20},
                                            {"dimension_values": ["CA"], "value": 10},
                                        ]
                                    }
                                ]
                            },
                        }
                    ]
                }
            return {"data": []}

        mock_safe_json_get.side_effect = _record
        demographics = service.fetch_demographics(connection, "1789")

        self.assertEqual(len(calls), 3)
        for url, params in calls:
            self.assertEqual(urlparse(url).netloc, "graph.instagram.com")
            self.assertEqual(params.get("metric"), "follower_demographics")
            self.assertEqual(params.get("period"), "lifetime")
            self.assertEqual(params.get("metric_type"), "total_value")
        self.assertEqual(demographics["audience_country"], [{"label": "US", "value": 20}, {"label": "CA", "value": 10}])

    def test_media_engagement_totals_aggregate_from_media_insights(self):
        service = InstagramAnalyticsService(user=None)
        media_insights = [
            {"metrics": [{"name": "likes", "value": 2}, {"name": "comments", "value": 1}]},
            {"metrics": [{"name": "likes", "value": 3}, {"name": "shares", "value": 4}]},
            {"metrics": [{"name": "saved", "value": 5}, {"name": "views", "value": 6}]},
        ]

        totals = service.fetch_engagement_metrics(media_insights)

        self.assertEqual(
            totals,
            {"likes": 5, "comments": 1, "saved": 5, "shares": 4, "views": 6},
        )
