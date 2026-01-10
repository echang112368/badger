from django.db import models
from django.contrib.auth import get_user_model

from merchants.models import ItemGroup, MerchantItem

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


REQUEST_STATUS_PENDING = "pending"
REQUEST_STATUS_ACCEPTED = "accepted"
REQUEST_STATUS_DECLINED = "declined"
REQUEST_STATUS_CHOICES = [
    (REQUEST_STATUS_PENDING, "Pending"),
    (REQUEST_STATUS_ACCEPTED, "Accepted"),
    (REQUEST_STATUS_DECLINED, "Declined"),
]


class PartnershipRequest(models.Model):
    creator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="partnership_requests",
        limit_choices_to={"is_creator": True},
    )
    merchant = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="incoming_requests",
        limit_choices_to={"is_merchant": True},
    )
    item = models.ForeignKey(
        MerchantItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partnership_requests",
    )
    item_group = models.ForeignKey(
        ItemGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partnership_requests",
    )
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=REQUEST_STATUS_CHOICES,
        default=REQUEST_STATUS_PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.creator.username} → {self.merchant.username} ({self.status})"

    @property
    def request_source(self) -> str:
        if self.item_id or self.item_group_id:
            return "Item"
        return "Business"
