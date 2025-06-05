from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()

class MerchantUser(User):
    class Meta:
        proxy = True
        verbose_name = 'Merchant'
        verbose_name_plural = 'Merchants'

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

    def __str__(self):
        return f"{self.creator.username} → {self.merchant.username}"