from django.db import models
from django.contrib.auth import get_user_model
from django.db.models import Sum
from decimal import Decimal, ROUND_HALF_UP

User = get_user_model()

class LedgerEntry(models.Model):
    ENTRY_TYPES = [
        ("commission", "Commission"),
        ("payout", "Payout"),
        ("payment", "Payment"),
    ]

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
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPES)
    timestamp = models.DateTimeField(auto_now_add=True)
    paid = models.BooleanField(default=False)

    def __str__(self):
        target = self.creator if self.creator else self.merchant
        return f"{self.entry_type} {self.amount} for {target}"

    @staticmethod
    def creator_balance(user):
        result = (
            LedgerEntry.objects.filter(creator=user).aggregate(total=Sum("amount"))
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
