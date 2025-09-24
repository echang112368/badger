from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

STATUS_REQUESTED = "requested"
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_CHOICES = [
    (STATUS_REQUESTED, "Requested"),
    (STATUS_ACTIVE, "Active"),
    (STATUS_INACTIVE, "Inactive"),
]


class MerchantCreatorLink(models.Model):
    merchant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='creator_links',
        limit_choices_to={'is_merchant': True}
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='merchant_links',
        limit_choices_to={'is_creator': True}
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
    )
    inactive_seen = models.BooleanField(default=True)
    status_updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.creator.username} → {self.merchant.username}"

    def save(self, *args, **kwargs):
        previous_status = None
        if self.pk:
            try:
                previous_status = (
                    MerchantCreatorLink.objects.only("status").get(pk=self.pk).status
                )
            except MerchantCreatorLink.DoesNotExist:
                previous_status = None

        status_changed = previous_status != self.status

        if status_changed:
            if self.status == STATUS_INACTIVE:
                self.inactive_seen = False
            else:
                self.inactive_seen = True

        super().save(*args, **kwargs)