from django.db import models
from django.contrib.auth import get_user_model
from django.db.models import Sum

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

    def __str__(self):
        target = self.creator if self.creator else self.merchant
        return f"{self.entry_type} {self.amount} for {target}"

    @staticmethod
    def creator_balance(user):
        result = (
            LedgerEntry.objects.filter(creator=user).aggregate(total=Sum("amount"))
        )
        return result["total"] or 0

    @staticmethod
    def merchant_balance(user):
        result = (
            LedgerEntry.objects.filter(merchant=user).aggregate(total=Sum("amount"))
        )
        return result["total"] or 0
