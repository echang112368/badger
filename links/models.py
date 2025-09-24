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

    def __str__(self):
        return f"{self.creator.username} → {self.merchant.username}"