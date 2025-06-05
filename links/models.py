from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

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
    status = models.CharField(max_length=20, default='active')

    def __str__(self):
        return f"{self.creator.username} → {self.merchant.username}"