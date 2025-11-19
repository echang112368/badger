from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """Custom user model with Badger specific roles."""

    email = models.EmailField(unique=True)
    is_merchant = models.BooleanField(default=False)
    is_creator = models.BooleanField(default=False)
    automatic_creator = models.BooleanField(
        default=False,
        help_text="Designates the built-in Badger creator that is auto-linked to merchants.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["automatic_creator"],
                condition=models.Q(automatic_creator=True),
                name="unique_automatic_creator",
            )
        ]

    def save(self, *args, **kwargs):
        """Persist the user while keeping automatic creator state in sync."""

        previous_auto = None
        if self.pk:
            previous_auto = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("automatic_creator", flat=True)
                .first()
            )
        auto_changed = False
        if self.pk:
            auto_changed = previous_auto != self.automatic_creator
        else:
            auto_changed = bool(self.automatic_creator)
        self._automatic_creator_changed = bool(auto_changed)

        if self.automatic_creator and not self.is_creator:
            self.is_creator = True

        super().save(*args, **kwargs)
