from decimal import Decimal

from django.test import TestCase

from accounts.models import CustomUser
from merchants.models import MerchantMeta

from .models import LedgerEntry
from .invoices import generate_all_invoices


class InvoiceGenerationTests(TestCase):
    def setUp(self):
        self.merchant = CustomUser.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="pass",
            is_merchant=True,
        )

        meta = MerchantMeta.objects.get(user=self.merchant)
        meta.company_name = "Acme Co"
        meta.paypal_email = "billing@acme.test"
        meta.monthly_fee = Decimal("0.00")
        meta.save()

    def test_generate_all_invoices_requires_monthly_fee(self):
        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-10.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        with self.assertRaises(RuntimeError) as exc:
            generate_all_invoices(ignore_date=True)

        self.assertIn("Acme Co", str(exc.exception))
