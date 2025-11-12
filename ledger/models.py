from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Sum
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from merchants.models import MerchantMeta

User = get_user_model()


class MerchantInvoice(models.Model):
    class Provider(models.TextChoices):
        PAYPAL = "paypal", "PayPal"
        SHOPIFY = "shopify", "Shopify"

    merchant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="invoices",
        limit_choices_to={"is_merchant": True},
    )
    provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.PAYPAL,
    )
    paypal_invoice_id = models.CharField(max_length=64, blank=True, null=True)
    paypal_invoice_url = models.URLField(blank=True, null=True)
    status = models.CharField(max_length=32, default="DRAFT")
    created_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shopify_charge_id = models.CharField(max_length=64, blank=True, null=True)
    shopify_status = models.CharField(max_length=64, blank=True)
    shopify_payload = models.JSONField(blank=True, default=dict)

    def __str__(self):
        provider_label = self.get_provider_display() or "Invoice"
        reference = self.paypal_invoice_id or self.shopify_charge_id or self.id
        return f"{provider_label} invoice {reference} for {self.merchant}"

    @property
    def payment_link(self) -> Optional[str]:
        """Return the stored PayPal payment link for the invoice."""

        if self.provider == self.Provider.SHOPIFY:
            return None

        if self.paypal_invoice_url:
            return self.paypal_invoice_url
        if self.paypal_invoice_id:
            return f"https://www.paypal.com/invoice/p/#{self.paypal_invoice_id}"
        return None


class MerchantMonthlyFee(MerchantMeta):
    """Proxy model to expose monthly fee management within the ledger app."""

    class Meta:
        proxy = True
        app_label = "ledger"
        verbose_name = "Merchant Monthly Fee"
        verbose_name_plural = "Merchant Monthly Fees"


class LedgerEntry(models.Model):
    class EntryType(models.TextChoices):
        COMMISSION = "commission", "Commission"
        PAYOUT = "payout", "Payout"
        PAYMENT = "payment", "Payment"
        POINTS = "points", "Points"
        AFFILIATE_PAYOUT = "affiliate_payout", "Affiliate Payout"
        BADGER_PAYOUT = "badger_payout", "Badger Payout"

    creator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ledger_entries_as_creator",
        limit_choices_to={"is_creator": True},
    )
    merchant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ledger_entries_as_merchant",
        limit_choices_to={"is_merchant": True},
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    timestamp = models.DateTimeField(auto_now_add=True)
    paid = models.BooleanField(default=False)
    invoice = models.ForeignKey(
        'MerchantInvoice',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='entries',
    )

    def __str__(self):
        target = self.creator if self.creator else self.merchant
        return f"{self.entry_type} {self.amount} for {target}"

    @staticmethod
    def creator_balance(user):
        result = (
            LedgerEntry.objects.filter(creator=user, paid=False).aggregate(
                total=Sum("amount")
            )
        )
        total = result["total"] if result["total"] is not None else Decimal("0")
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def merchant_balance(user):
        result = (
            LedgerEntry.objects.filter(merchant=user).aggregate(total=Sum("amount"))
        )
        total = result["total"] if result["total"] is not None else Decimal("0")
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
