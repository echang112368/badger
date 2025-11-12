from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import CustomUser
from merchants.models import MerchantMeta
from shopify_app.billing import ShopifyChargeDetails

from .models import LedgerEntry, MerchantInvoice
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
        mock_usage_charge.return_value = ShopifyChargeDetails(
            charge_id="55",
            amount=Decimal("25.00"),
            currency="USD",
            status="processed",
            name="Usage",
            description="Monthly",
            raw={"id": 55},
        )

        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-25.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        invoice = create_invoice_for_merchant(self.merchant)

        self.assertIsInstance(invoice, MerchantInvoice)
        self.assertEqual(invoice.provider, MerchantInvoice.Provider.SHOPIFY)
        self.assertEqual(invoice.shopify_charge_id, "55")
        self.assertEqual(invoice.total_amount, Decimal("25.00"))
        mock_usage_charge.assert_called_once()
        entries = LedgerEntry.objects.filter(merchant=self.merchant)
        self.assertTrue(entries.exists())
        self.assertTrue(all(entry.paid for entry in entries))
        self.assertTrue(all(entry.invoice == invoice for entry in entries))

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_generate_all_invoices_shopify(self, mock_usage_charge):
        mock_usage_charge.return_value = ShopifyChargeDetails(
            charge_id="77",
            amount=Decimal("15.00"),
            currency="USD",
            status="processed",
            name="Usage",
            description="Monthly",
            raw={"id": 77},
        )
        LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-15.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        invoices = generate_all_invoices(ignore_date=True)

        mock_usage_charge.assert_called_once()
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0].provider, MerchantInvoice.Provider.SHOPIFY)

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_generate_all_invoices_shopify_only_skips_non_shopify(self, mock_usage_charge):
        mock_usage_charge.return_value = ShopifyChargeDetails(
            charge_id="90",
            amount=Decimal("12.00"),
            currency="USD",
            status="processed",
            name="Usage",
            description="Monthly",
            raw={"id": 90},
        )
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

        invoices = generate_all_invoices(ignore_date=True, shopify_only=True)

        mock_usage_charge.assert_called_once()
        self.assertEqual(len(invoices), 1)
        self.assertTrue(
            LedgerEntry.objects.filter(merchant=other, paid=False).exists(),
            "Non-Shopify merchants should be ignored when shopify_only is True",
        )

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_shopify_invoice_includes_monthly_affiliate_and_special_fees(
        self, mock_usage_charge
    ):
        affiliate_entry = LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-100.00"),
            entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT,
        )
        special_uuid_entry = LedgerEntry.objects.create(
            merchant=self.merchant,
            amount=Decimal("-15.00"),
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
        )

        expected_total = Decimal("130.00")
        mock_usage_charge.return_value = ShopifyChargeDetails(
            charge_id="101",
            amount=expected_total,
            currency="USD",
            status="processed",
            name="Usage",
            description="Outstanding",
            raw={"id": 101},
        )

        invoice = create_invoice_for_merchant(self.merchant)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.total_amount, expected_total)

        mock_usage_charge.assert_called_once()
        self.assertEqual(mock_usage_charge.call_args.kwargs["amount"], expected_total)

        entries = list(LedgerEntry.objects.filter(merchant=self.merchant))
        self.assertTrue(all(entry.paid for entry in entries))
        self.assertTrue(all(entry.invoice == invoice for entry in entries))

        monthly_entry = next(
            entry
            for entry in entries
            if entry.entry_type == LedgerEntry.EntryType.BADGER_PAYOUT
            and entry.amount == Decimal("-10.00")
        )
        processing_entry = next(
            entry
            for entry in entries
            if entry.entry_type == LedgerEntry.EntryType.BADGER_PAYOUT
            and entry.amount == Decimal("-5.00")
        )

        self.assertIsNotNone(monthly_entry)
        self.assertIsNotNone(processing_entry)

        affiliate_entry.refresh_from_db()
        special_uuid_entry.refresh_from_db()
        self.assertEqual(affiliate_entry.invoice, invoice)
        self.assertEqual(special_uuid_entry.invoice, invoice)

    @patch("ledger.invoices.shopify_billing.create_usage_charge")
    def test_shopify_only_monthly_fee_creates_usage_charge(self, mock_usage_charge):
        expected_total = Decimal("10.00")
        mock_usage_charge.return_value = ShopifyChargeDetails(
            charge_id="202",
            amount=expected_total,
            currency="USD",
            status="processed",
            name="Usage",
            description="Monthly",
            raw={"id": 202},
        )

        invoice = create_invoice_for_merchant(self.merchant)

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.total_amount, expected_total)

        mock_usage_charge.assert_called_once()
        self.assertEqual(mock_usage_charge.call_args.kwargs["amount"], expected_total)

        entries = list(LedgerEntry.objects.filter(merchant=self.merchant))
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].paid)
        self.assertEqual(entries[0].invoice, invoice)
