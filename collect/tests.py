from django.test import TestCase, RequestFactory
from django.urls import reverse
from decimal import Decimal
import json
import uuid

from accounts.models import CustomUser
from merchants.models import MerchantMeta, MerchantItem, ItemGroup
from creators.models import CreatorMeta
from customer.models import CustomerMeta
from ledger.models import LedgerEntry
from collect.models import (
    AffiliateClick,
    ReferralVisit,
    ReferralConversion,
    CreatorMerchantStatus,
)
from .views import orders_create_webhook


class OrdersWebhookTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _setup_users(self):
        merchant = CustomUser.objects.create_user(
            username="merchant",
            email="m@example.com",
            password="pass",
            is_merchant=True,
        )
        creator = CustomUser.objects.create_user(
            username="creator",
            email="c@example.com",
            password="pass",
            is_creator=True,
        )
        customer = CustomUser.objects.create_user(
            username="customer",
            email="u@example.com",
            password="pass",
        )

        merchant_meta = MerchantMeta.objects.get(user=merchant)
        merchant_meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        merchant_meta.shopify_billing_status = "active"
        merchant_meta.save()

        return (
            merchant,
            creator,
            customer,
            merchant_meta,
            CreatorMeta.objects.get(user=creator),
            CustomerMeta.objects.get(user=customer),
        )

    def _call_webhook(self, merchant_meta, creator_meta, customer_meta, line_items, total_price):
        payload = {
            "total_price": str(total_price),
            "line_items": line_items,
            "note_attributes": [
                {"name": "uuid", "value": str(creator_meta.uuid)},
                {"name": "storeID", "value": str(merchant_meta.uuid)},
            ],
        }

        if customer_meta:
            payload["note_attributes"].append(
                {"name": "cusID", "value": str(customer_meta.uuid)}
            )
        body = json.dumps(payload)
        request = self.factory.post(
            "/shopify/webhooks/orders-create/",
            data=body,
            content_type="application/json",
        )
        return orders_create_webhook(request)

    def test_commission_and_points_recorded(self):
        merchant, creator, customer, merchant_meta, creator_meta, customer_meta = (
            self._setup_users()
        )

        shirt = MerchantItem.objects.create(
            merchant=merchant,
            title="Shirt",
            link="http://shirt",
            shopify_product_id="1",
        )
        pants = MerchantItem.objects.create(
            merchant=merchant,
            title="Pants",
            link="http://pants",
            shopify_product_id="2",
        )

        group1 = ItemGroup.objects.create(
            merchant=merchant,
            name="Group 1",
            affiliate_percent=Decimal("3"),
        )
        group1.items.add(shirt)

        group2 = ItemGroup.objects.create(
            merchant=merchant,
            name="Group 2",
            affiliate_percent=Decimal("4"),
        )
        group2.items.add(pants)

        line_items = [
            {"product_id": 1, "quantity": 2, "price": "10.00"},
            {"product_id": 2, "quantity": 1, "price": "5.00"},
        ]

        response = self._call_webhook(
            merchant_meta,
            creator_meta,
            customer_meta,
            line_items,
            Decimal("25.00"),
        )
        self.assertEqual(response.status_code, 200)

        creator_entry = LedgerEntry.objects.get(
            creator=creator,
            merchant=merchant,
            entry_type=LedgerEntry.EntryType.COMMISSION,
        )
        merchant_entry = LedgerEntry.objects.get(
            merchant=merchant,
            creator__isnull=True,
            entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT,
        )
        points_entry = LedgerEntry.objects.get(creator=customer, entry_type="points")
        conversion = ReferralConversion.objects.get()

        self.assertEqual(creator_entry.merchant, merchant)
        self.assertEqual(creator_entry.amount, Decimal("0.80"))
        self.assertIsNone(merchant_entry.creator)
        self.assertEqual(merchant_entry.amount, Decimal("-0.80"))
        self.assertEqual(points_entry.amount, Decimal("48"))
        self.assertIsNone(points_entry.merchant)
        self.assertFalse(
            LedgerEntry.objects.filter(merchant=merchant, entry_type="points").exists()
        )
        self.assertEqual(conversion.creator, creator)
        self.assertEqual(conversion.merchant, merchant)
        self.assertEqual(conversion.order_amount, Decimal("25.00"))
        self.assertEqual(conversion.commission_amount, Decimal("0.80"))

    def test_inactive_billing_blocks_ledger_updates(self):
        merchant, creator, customer, merchant_meta, creator_meta, customer_meta = (
            self._setup_users()
        )

        merchant_meta.shopify_billing_status = ""
        merchant_meta.save()

        line_items = [
            {"product_id": 1, "quantity": 1, "price": "10.00"},
        ]

        response = self._call_webhook(
            merchant_meta,
            creator_meta,
            customer_meta,
            line_items,
            Decimal("10.00"),
        )

        self.assertEqual(response.status_code, 402)
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertEqual(ReferralConversion.objects.count(), 0)

    def test_inactive_creator_skips_income_and_conversion(self):
        merchant, creator, customer, merchant_meta, creator_meta, customer_meta = (
            self._setup_users()
        )

        CreatorMerchantStatus.objects.create(
            creator=creator_meta, merchant=merchant_meta, is_active=False
        )

        shirt = MerchantItem.objects.create(
            merchant=merchant,
            title="Shirt",
            link="http://shirt",
            shopify_product_id="1",
        )
        group = ItemGroup.objects.create(
            merchant=merchant,
            name="Group",
            affiliate_percent=Decimal("10"),
        )
        group.items.add(shirt)

        line_items = [{"product_id": 1, "quantity": 1, "price": "10.00"}]

        response = self._call_webhook(
            merchant_meta,
            creator_meta,
            customer_meta,
            line_items,
            Decimal("10.00"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            LedgerEntry.objects.filter(
                creator=creator, entry_type=LedgerEntry.EntryType.COMMISSION
            ).exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(
                merchant=merchant,
                entry_type__in=[
                    LedgerEntry.EntryType.AFFILIATE_PAYOUT,
                    LedgerEntry.EntryType.BADGER_PAYOUT,
                ],
            ).exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(creator=customer, entry_type="points").exists()
        )
        self.assertEqual(ReferralConversion.objects.count(), 0)

    def test_special_uuid_triggers_fixed_commission(self):
        (
            merchant,
            creator,
            customer,
            merchant_meta,
            creator_meta,
            customer_meta,
        ) = self._setup_users()

        creator_meta.uuid = uuid.UUID(
            "733d0d67-6a30-4c48-a92e-b8e211b490f5"
        )
        creator_meta.save()

        shirt = MerchantItem.objects.create(
            merchant=merchant,
            title="Shirt",
            link="http://shirt",
            shopify_product_id="1",
        )
        group = ItemGroup.objects.create(
            merchant=merchant,
            name="Group",
            affiliate_percent=Decimal("3"),
        )
        group.items.add(shirt)

        line_items = [{"product_id": 1, "quantity": 1, "price": "100.00"}]

        response = self._call_webhook(
            merchant_meta,
            creator_meta,
            customer_meta,
            line_items,
            Decimal("100.00"),
        )
        self.assertEqual(response.status_code, 200)

        creator_entry = LedgerEntry.objects.get(
            creator=creator,
            merchant=merchant,
            entry_type=LedgerEntry.EntryType.COMMISSION,
        )
        merchant_entry = LedgerEntry.objects.get(
            merchant=merchant,
            creator__isnull=True,
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )
        points_entry = LedgerEntry.objects.get(
            creator=customer, entry_type="points"
        )
        conversion = ReferralConversion.objects.get()

        self.assertEqual(creator_entry.merchant, merchant)
        self.assertEqual(creator_entry.amount, Decimal("5.00"))
        self.assertIsNone(merchant_entry.creator)
        self.assertEqual(merchant_entry.amount, Decimal("-5.00"))
        self.assertEqual(points_entry.amount, Decimal("300"))
        self.assertEqual(conversion.commission_amount, Decimal("5.00"))

    def test_missing_customer_skips_points(self):
        merchant, creator, customer, merchant_meta, creator_meta, customer_meta = (
            self._setup_users()
        )
        shirt = MerchantItem.objects.create(
            merchant=merchant,
            title="Shirt",
            link="http://shirt",
            shopify_product_id="1",
        )
        group = ItemGroup.objects.create(
            merchant=merchant,
            name="Group",
            affiliate_percent=Decimal("5"),
        )
        group.items.add(shirt)

        line_items = [{"product_id": 1, "quantity": 1, "price": "20.00"}]

        response = self._call_webhook(
            merchant_meta,
            creator_meta,
            None,
            line_items,
            Decimal("20.00"),
        )
        self.assertEqual(response.status_code, 200)

        self.assertTrue(
            LedgerEntry.objects.filter(
                creator=creator, entry_type=LedgerEntry.EntryType.COMMISSION
            ).exists()
        )
        self.assertTrue(
            LedgerEntry.objects.filter(
                merchant=merchant,
                entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT,
            ).exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(creator=customer, entry_type="points").exists()
        )
        self.assertEqual(ReferralConversion.objects.count(), 1)

    def test_item_without_group_has_zero_commission(self):
        merchant, creator, customer, merchant_meta, creator_meta, customer_meta = (
            self._setup_users()
        )

        # Item exists but is not assigned to any group
        MerchantItem.objects.create(
            merchant=merchant,
            title="Hat",
            link="http://hat",
            shopify_product_id="1",
        )

        line_items = [{"product_id": 1, "quantity": 1, "price": "20.00"}]

        response = self._call_webhook(
            merchant_meta,
            creator_meta,
            customer_meta,
            line_items,
            Decimal("20.00"),
        )
        self.assertEqual(response.status_code, 200)

        # No commissions or points should be recorded when item lacks a group
        self.assertFalse(
            LedgerEntry.objects.filter(
                creator=creator, entry_type=LedgerEntry.EntryType.COMMISSION
            ).exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(
                merchant=merchant,
                entry_type__in=[
                    LedgerEntry.EntryType.AFFILIATE_PAYOUT,
                    LedgerEntry.EntryType.BADGER_PAYOUT,
                ],
            ).exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(creator=customer, entry_type="points").exists()
        )
        conversion = ReferralConversion.objects.get()
        self.assertEqual(conversion.order_amount, Decimal("20.00"))
        self.assertEqual(conversion.commission_amount, Decimal("0.00"))


class ReferralTrackingTests(TestCase):
    def setUp(self):
        self.merchant = CustomUser.objects.create_user(
            username="merchant-analytics",
            email="merchant.analytics@example.com",
            password="pass",
            is_merchant=True,
        )
        self.creator = CustomUser.objects.create_user(
            username="creator-analytics",
            email="creator.analytics@example.com",
            password="pass",
            is_creator=True,
        )
        self.merchant_meta = MerchantMeta.objects.get(user=self.merchant)
        self.creator_meta = CreatorMeta.objects.get(user=self.creator)
        self.merchant_meta.shopify_store_domain = "merchant.test"
        self.merchant_meta.save()

    def test_track_referral_visit_records_event(self):
        url = reverse("collect_track_visit")
        payload = {
            "creator_uuid": str(self.creator_meta.uuid),
            "merchant_uuid": str(self.merchant_meta.uuid),
            "merchant_domain": "merchant.test",
            "landing_url": "https://merchant.test/products/widget?ref=badger:%s"
            % self.creator_meta.uuid,
            "landing_path": "/products/widget",
            "query_string": "?ref=badger:%s" % self.creator_meta.uuid,
            "query_params": {"ref": f"badger:{self.creator_meta.uuid}"},
            "referrer": "https://creator.example/",
            "visitor_id": "visitor-123",
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_ORIGIN="https://scripts.example",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReferralVisit.objects.count(), 1)
        visit = ReferralVisit.objects.first()
        self.assertEqual(str(visit.creator_uuid), str(self.creator_meta.uuid))
        self.assertEqual(str(visit.merchant_uuid), str(self.merchant_meta.uuid))
        self.assertEqual(visit.creator, self.creator)
        self.assertEqual(visit.merchant, self.merchant)
        self.assertEqual(visit.merchant_domain, "merchant.test")
        self.assertEqual(visit.visitor_id, "visitor-123")
        self.assertEqual(visit.query_params.get("ref"), f"badger:{self.creator_meta.uuid}")
        self.assertEqual(visit.query_string, payload["query_string"])
        self.assertEqual(visit.landing_url, payload["landing_url"])
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")

    def test_track_visit_records_affiliate_click(self):
        url = reverse("collect_track_visit")
        payload = {
            "uuid": str(self.creator_meta.uuid),
            "storeID": str(self.merchant_meta.uuid),
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AffiliateClick.objects.count(), 1)
        click = AffiliateClick.objects.first()
        self.assertEqual(str(click.uuid), payload["uuid"])
        self.assertEqual(str(click.storeID), payload["storeID"])

        body = json.loads(response.content)
        self.assertEqual(body.get("total_clicks"), 1)
        self.assertIn("received creator", body.get("debug", ""))

    def test_invalid_payload_returns_error(self):
        url = reverse("collect_track_visit")
        response = self.client.post(
            url,
            data=json.dumps({"creator_uuid": "invalid"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ReferralVisit.objects.count(), 0)

    def test_affiliate_click_invalid_payload_returns_error(self):
        url = reverse("collect_track_visit")
        response = self.client.post(
            url,
            data=json.dumps({"uuid": "invalid", "storeID": str(self.merchant_meta.uuid)}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(AffiliateClick.objects.count(), 0)

    def test_affiliate_click_missing_store_returns_not_found(self):
        url = reverse("collect_track_visit")
        payload = {
            "uuid": str(self.creator_meta.uuid),
            "storeID": str(uuid.uuid4()),
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(AffiliateClick.objects.count(), 0)

    def test_affiliate_click_missing_creator_returns_not_found(self):
        url = reverse("collect_track_visit")
        payload = {
            "uuid": str(uuid.uuid4()),
            "storeID": str(self.merchant_meta.uuid),
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(AffiliateClick.objects.count(), 0)

    def test_options_request_returns_cors_headers(self):
        url = reverse("collect_track_visit")
        response = self.client.options(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")
