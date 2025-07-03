from django.db import models
from accounts.models import CustomUser

class MerchantMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    company_name = models.CharField(max_length=255, blank=True)
    buisID = models.PositiveIntegerField(unique=True, editable=False, null=True)

    def save(self, *args, **kwargs):
        if self.buisID is None and self.user_id:
            self.buisID = self.user_id
        super().save(*args, **kwargs)

    def __str__(self):
        return self.company_name
    
class MerchantItem(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete = models.CASCADE)
    title = models.CharField(max_length =255)
    link = models.URLField()
    created_at = models.DateTimeField(auto_now_add = True)

    def __str__(self):
        return self.title
    