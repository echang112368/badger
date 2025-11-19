from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """Custom user model with unique email field."""

    email = models.EmailField(unique=True)
    is_merchant = models.BooleanField(default=False)
    is_creator = models.BooleanField(default=False)
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
