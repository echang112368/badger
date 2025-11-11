from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import CustomUser
from merchants.models import MerchantMeta

from .models import LedgerEntry
from .invoices import generate_all_invoices, create_invoice_for_merchant


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


class ShopifyInvoiceTests(TestCase):
    def setUp(self):
        self.merchant = CustomUser.objects.create_user(
            username="shopify_merchant",
            email="shopify@example.com",
            password="pass",
            is_merchant=True,
        )
        self.meta = MerchantMeta.objects.get(user=self.merchant)
        self.meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        self.meta.shopify_access_token = "token"
        self.meta.shopify_store_domain = "shopify.test"
        self.meta.monthly_fee = Decimal("10.00")
        self.meta.shopify_recurring_charge_id = "123"
        self.meta.shopify_billing_status = "active"
        self.meta.save()

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_create_invoice_uses_shopify_billing(self, mock_usage_charge):
        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-25.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        invoice = create_invoice_for_merchant(self.merchant)

        self.assertIsNone(invoice)
        mock_usage_charge.assert_called_once()
        entries = LedgerEntry.objects.filter(merchant=self.merchant)
        self.assertTrue(entries.exists())
        self.assertTrue(all(entry.paid for entry in entries))

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_generate_all_invoices_shopify(self, mock_usage_charge):
        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-15.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        generate_all_invoices(ignore_date=True)

        mock_usage_charge.assert_called_once()

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_generate_all_invoices_shopify_only_skips_non_shopify(self, mock_usage_charge):
        other = CustomUser.objects.create_user(
            username="other", email="other@example.com", password="pass", is_merchant=True
        )
        other_meta = MerchantMeta.objects.get(user=other)
        other_meta.monthly_fee = Decimal("0.00")
        other_meta.paypal_email = ""
        other_meta.save()

        LedgerEntry.objects.create(
            merchant=other,
            amount=Decimal("-20.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-12.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        generate_all_invoices(ignore_date=True, shopify_only=True)

        mock_usage_charge.assert_called_once()
        self.assertTrue(
            LedgerEntry.objects.filter(merchant=other, paid=False).exists(),
            "Non-Shopify merchants should be ignored when shopify_only is True",
        )
