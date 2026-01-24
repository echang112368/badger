from django.contrib.auth import get_user_model
from django.db.models import Sum
from decimal import Decimal

from ledger.models import LedgerEntry

LEVEL_STEP_POINTS = 1000


def get_points_balance(user: get_user_model()) -> int:
    """Return the current rewards points for ``user``.

    Points are stored as ledger entries with ``entry_type="points"``.
    """

    total = (
        LedgerEntry.objects.filter(creator=user, entry_type="points")
        .aggregate(total=Sum("amount"))
        .get("total")
        or Decimal("0")
    )
    return int(total)


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


def get_level_progress(points: int, step_points: int = LEVEL_STEP_POINTS) -> dict:
    """Return level progress data for the given points.

    Levels are spaced out by ``step_points``. Progress resets to 0 whenever
    the user reaches a new level.
    """

    safe_points = max(int(points), 0)
    step_points = max(int(step_points), 1)
    level = safe_points // step_points + 1
    points_into_level = safe_points % step_points
    progress_ratio = round(points_into_level / step_points, 4)
    progress_percent = round(progress_ratio * 100, 2)

    return {
        "level": level,
        "level_points": points_into_level,
        "level_points_max": step_points,
        "level_progress": progress_ratio,
        "level_progress_percent": progress_percent,
        "points_to_next_level": step_points - points_into_level,
    }
