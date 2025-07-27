from django.db import models
import uuid

class CreatorMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    @property
    def int_uuid(self):
        """Return the UUID as an integer with dashes removed."""
        try:
            return int(str(self.uuid).replace("-", ""))
        except (TypeError, ValueError):
            return None

    def __str__(self):
        return self.user.username

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)

