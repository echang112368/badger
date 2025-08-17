from django.contrib.auth import get_user_model


def get_points_balance(user: get_user_model()) -> int:
    """Return the current rewards points for ``user``.

    Points are derived from the creator ledger balance. Each dollar of
    unpaid commission equates to 100 points. Using this helper keeps the
    login API and dashboard in sync with the ledger.
    """

    from ledger.models import LedgerEntry

    balance = LedgerEntry.creator_balance(user)
    return int(balance * 100)

