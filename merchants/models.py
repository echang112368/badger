from django.db import models
from accounts.models import CustomUser
import uuid

class MerchantMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    company_name = models.CharField(max_length=255, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def __str__(self):
        return self.company_name

class MerchantItem(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    link = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
