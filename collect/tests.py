from django.test import TestCase, RequestFactory
from decimal import Decimal
import json
import uuid

from accounts.models import CustomUser
from merchants.models import MerchantMeta, MerchantItem, ItemGroup
from creators.models import CreatorMeta
from customer.models import CustomerMeta
from ledger.models import LedgerEntry
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

        creator_entry = LedgerEntry.objects.get(creator=creator, entry_type="commission")
        merchant_entry = LedgerEntry.objects.get(merchant=merchant, entry_type="commission")
        points_entry = LedgerEntry.objects.get(creator=customer, entry_type="points")

        self.assertEqual(creator_entry.amount, Decimal("0.80"))
        self.assertEqual(merchant_entry.amount, Decimal("-0.80"))
        self.assertEqual(points_entry.amount, Decimal("48"))
        self.assertIsNone(points_entry.merchant)
        self.assertFalse(
            LedgerEntry.objects.filter(merchant=merchant, entry_type="points").exists()
        )

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
            creator=creator, entry_type="commission"
        )
        merchant_entry = LedgerEntry.objects.get(
            merchant=merchant, entry_type="commission"
        )
        points_entry = LedgerEntry.objects.get(
            creator=customer, entry_type="points"
        )

        self.assertEqual(creator_entry.amount, Decimal("5.00"))
        self.assertEqual(merchant_entry.amount, Decimal("-5.00"))
        self.assertEqual(points_entry.amount, Decimal("300"))

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
            LedgerEntry.objects.filter(creator=creator, entry_type="commission").exists()
        )
        self.assertTrue(
            LedgerEntry.objects.filter(merchant=merchant, entry_type="commission").exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(creator=customer, entry_type="points").exists()
        )

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
            LedgerEntry.objects.filter(creator=creator, entry_type="commission").exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(merchant=merchant, entry_type="commission").exists()
        )
        self.assertFalse(
            LedgerEntry.objects.filter(creator=customer, entry_type="points").exists()
        )
