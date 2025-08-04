from django.db import models
from merchants.models import MerchantMeta
from utils.encryption import encode_api_value, decode_api_value


class ShopifyCredential(models.Model):
    merchant = models.OneToOneField(MerchantMeta, on_delete=models.CASCADE, related_name="shopify_credential")
    api_key_encoded = models.CharField(max_length=255, blank=True)
    api_password_encoded = models.CharField(max_length=255, blank=True)

    def set_api_key(self, value: str) -> None:
        self.api_key_encoded = encode_api_value(value)

    def get_api_key(self) -> str:
        return decode_api_value(self.api_key_encoded)

    def set_api_password(self, value: str) -> None:
        self.api_password_encoded = encode_api_value(value)

    def get_api_password(self) -> str:
        return decode_api_value(self.api_password_encoded)

    def __str__(self) -> str:
        return f"Shopify Credential for {self.merchant.user}" if self.merchant else "Shopify Credential"
