from django.contrib.auth import get_user_model
from django.db.models import Sum
from decimal import Decimal

from ledger.models import LedgerEntry


def get_points_balance(user: get_user_model()) -> Decimal:
    """Return the current rewards points for ``user``.

    Points are stored as ledger entries with ``entry_type="points"``.
    """

    total = (
        LedgerEntry.objects.filter(creator=user, entry_type="points")
        .aggregate(total=Sum("amount"))
        .get("total")
        or Decimal("0")
    )
    return total.quantize(Decimal("0.1"))


def get_savings_total(user: get_user_model()) -> int:
    """Return the total savings amount for ``user``.

    Savings are stored as ledger entries with ``entry_type="savings"``.
    """

    total = (
        LedgerEntry.objects.filter(
            creator=user,
            entry_type=LedgerEntry.EntryType.SAVINGS,
        )
        .aggregate(total=Sum("amount"))
        .get("total")
        or Decimal("0")
    )
    return int(total)
