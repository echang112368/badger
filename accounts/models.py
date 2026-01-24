from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """Custom user model with unique email field."""

    email = models.EmailField(unique=True)
    is_merchant = models.BooleanField(default=False)
    is_creator = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    verification_code = models.CharField(max_length=6, blank=True, null=True)
    is_default_badger_creator = models.BooleanField(
        default=False,
        help_text=(
            "Marks this creator as the automatic Badger creator that is applied "
            "to merchant accounts. Only one user can be the default at a time."
        ),
    )

    @classmethod
    def get_default_badger_creator(cls):
        """Return the designated default Badger creator, if any."""

        return cls.objects.filter(
            is_default_badger_creator=True, is_creator=True
        ).first()

    @classmethod
    def ensure_badger_creator(cls):
        """Ensure the default Badger creator account exists."""

        from creators.constants import (
            BADGER_CREATOR_EMAIL,
            BADGER_CREATOR_USERNAME,
            BADGER_CREATOR_UUID,
        )
        from creators.models import CreatorMeta

        badger_meta = CreatorMeta.objects.filter(uuid=BADGER_CREATOR_UUID).first()
        if badger_meta:
            badger_user = badger_meta.user
        else:
            badger_user = cls.objects.filter(
                username=BADGER_CREATOR_USERNAME
            ).first()
            if not badger_user:
                badger_user = cls.objects.filter(
                    email=BADGER_CREATOR_EMAIL
                ).first()
            if not badger_user:
                badger_user = cls.objects.create_user(
                    username=BADGER_CREATOR_USERNAME,
                    email=BADGER_CREATOR_EMAIL,
                    password=None,
                    first_name="Badger",
                    last_name="",
                    is_creator=True,
                    is_default_badger_creator=True,
                )
                badger_user.set_unusable_password()
                badger_user.save(update_fields=["password"])

        if not badger_user.is_creator or not badger_user.is_default_badger_creator:
            badger_user.is_creator = True
            badger_user.is_default_badger_creator = True
            badger_user.save(update_fields=["is_creator", "is_default_badger_creator"])

        CreatorMeta.objects.update_or_create(
            user=badger_user, defaults={"uuid": BADGER_CREATOR_UUID}
        )

        return badger_user

    def save(self, *args, **kwargs):
        # Ensure only one default Badger creator exists at a time and that the
        # user has creator capabilities enabled.
        if self.is_default_badger_creator and not self.is_creator:
            self.is_creator = True

        super().save(*args, **kwargs)

        if self.is_default_badger_creator:
            type(self).objects.exclude(pk=self.pk).filter(
                is_default_badger_creator=True
            ).update(is_default_badger_creator=False)

            # Attach the default creator to all merchants whose plan includes it.
            try:
                from merchants.models import MerchantMeta

                for meta in MerchantMeta.objects.select_related("user"):
                    meta.ensure_badger_creator_link()
            except Exception:
                # Avoid interrupting save operations if migrations or imports
                # are not ready (e.g., during initial setup).
                pass
