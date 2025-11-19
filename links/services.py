from __future__ import annotations

from typing import Iterable, Optional

from django.contrib.auth import get_user_model

from .models import MerchantCreatorLink, STATUS_ACTIVE

User = get_user_model()


def get_automatic_creator_user() -> Optional[User]:
    """Return the globally configured automatic creator user if it exists."""

    return User.objects.filter(automatic_creator=True, is_creator=True).first()


def ensure_automatic_creator_for_merchant(merchant: User, creator: Optional[User] = None) -> None:
    """Ensure the automatic creator link exists for the given merchant."""

    if merchant is None:
        return

    creator_user = creator or get_automatic_creator_user()
    if not creator_user or creator_user == merchant:
        return

    link, created = MerchantCreatorLink.objects.get_or_create(
        merchant=merchant,
        creator=creator_user,
        defaults={"status": STATUS_ACTIVE},
    )
    if not created and link.status != STATUS_ACTIVE:
        link.status = STATUS_ACTIVE
        link.save(update_fields=["status"])


def remove_automatic_creator_for_merchant(merchant: User, creator: Optional[User] = None) -> None:
    """Remove the automatic creator from a specific merchant."""

    creator_user = creator or get_automatic_creator_user()
    if not merchant or not creator_user:
        return

    MerchantCreatorLink.objects.filter(merchant=merchant, creator=creator_user).delete()


def remove_creator_from_all_merchants(creator: User) -> None:
    """Remove a creator from all merchants regardless of plan."""

    if not creator:
        return

    MerchantCreatorLink.objects.filter(creator=creator).delete()


def ensure_automatic_creator_for_merchants(merchants: Iterable[User], creator: Optional[User] = None) -> None:
    """Ensure multiple merchants are linked to the automatic creator."""

    for merchant in merchants:
        ensure_automatic_creator_for_merchant(merchant, creator=creator)
