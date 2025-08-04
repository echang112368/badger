from django.db import models
from accounts.models import CustomUser
import uuid

class MerchantMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    company_name = models.CharField(max_length=255, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    affiliate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    paypal_email = models.EmailField(blank=True)
    contact_email = models.EmailField(blank=True)
    shopify_api_key = models.CharField(max_length=255, blank=True)
    shopify_api_password = models.CharField(max_length=255, blank=True)


    def __str__(self):
        return self.company_name

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.merchant_balance(self.user)

class MerchantItem(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    link = models.URLField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
