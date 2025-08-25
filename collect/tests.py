from django.test import TestCase, RequestFactory
from decimal import Decimal
import json

from accounts.models import CustomUser
from merchants.models import MerchantMeta
from creators.models import CreatorMeta
from customer.models import CustomerMeta
from ledger.models import LedgerEntry
from .views import orders_create_webhook


class OrdersWebhookTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _setup_users(self, percent=Decimal("10.00")):
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
        merchant_meta.affiliate_percent = percent
        merchant_meta.save()

        return (
            merchant,
            creator,
            customer,
            merchant_meta,
            CreatorMeta.objects.get(user=creator),
            CustomerMeta.objects.get(user=customer),
        )

    def _call_webhook(self, merchant_meta, creator_meta, customer_meta):
        payload = {
            "total_price": "26.30",
            "note_attributes": [
                {"name": "uuid", "value": str(creator_meta.uuid)},
                {"name": "storeID", "value": str(merchant_meta.uuid)},
                {"name": "cusID", "value": str(customer_meta.uuid)},
            ],
        }
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

        response = self._call_webhook(merchant_meta, creator_meta, customer_meta)
        self.assertEqual(response.status_code, 200)

        creator_entry = LedgerEntry.objects.get(creator=creator, entry_type="commission")
        merchant_entry = LedgerEntry.objects.get(merchant=merchant, entry_type="commission")
        points_entry = LedgerEntry.objects.get(creator=customer, entry_type="points")

        self.assertEqual(creator_entry.amount, Decimal("2.63"))
        self.assertEqual(merchant_entry.amount, Decimal("-2.63"))
        self.assertEqual(points_entry.amount, Decimal("157"))
        self.assertEqual(points_entry.merchant, merchant)

    def test_missing_customer_skips_points(self):
        merchant, creator, customer, merchant_meta, creator_meta, customer_meta = (
            self._setup_users()
        )
        payload = {
            "total_price": "26.30",
            "note_attributes": [
                {"name": "uuid", "value": str(creator_meta.uuid)},
                {"name": "storeID", "value": str(merchant_meta.uuid)},
            ],
        }
        body = json.dumps(payload)
        request = self.factory.post(
            "/shopify/webhooks/orders-create/",
            data=body,
            content_type="application/json",
        )
        response = orders_create_webhook(request)
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
