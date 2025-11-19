from __future__ import annotations

from typing import Optional
from uuid import UUID

from creators.models import CreatorMeta
from links.models import MerchantCreatorLink, STATUS_ACTIVE

BADGER_CREATOR_UUID = UUID("733d0d67-6a30-4c48-a92e-b8e211b490f5")


def get_badger_creator_user():
    """Return the user object tied to the Badger creator if it exists."""

    try:
        creator_meta = CreatorMeta.objects.select_related("user").get(
            uuid=BADGER_CREATOR_UUID
        )
    except CreatorMeta.DoesNotExist:
        return None
    return creator_meta.user


def get_badger_creator_id() -> Optional[int]:
    user = get_badger_creator_user()
    return getattr(user, "id", None)


def sync_badger_creator_link(merchant_user, *, should_exist: bool) -> bool:
    """Ensure the merchant has (or does not have) the Badger creator link."""

    badger_user = get_badger_creator_user()
    if badger_user is None:
        return False

    link_qs = MerchantCreatorLink.objects.filter(
        merchant=merchant_user, creator=badger_user
    )

    if should_exist:
        link, created = MerchantCreatorLink.objects.get_or_create(
            merchant=merchant_user,
            creator=badger_user,
            defaults={"status": STATUS_ACTIVE},
        )
        if not created and link.status != STATUS_ACTIVE:
            link.status = STATUS_ACTIVE
            link.save(update_fields=["status"])
        return True

    link_qs.delete()
    return True
