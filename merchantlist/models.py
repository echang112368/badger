from django.conf import settings
from django.db import models


class Merchant(models.Model):
    class MerchantType(models.TextChoices):
        INDEPENDENT = "independent", "Independent"
        SHOPIFY = "shopify", "Shopify"

    account = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="merchant_entries",
        null=True,
        blank=True,
    )
    account_name = models.CharField(max_length=255, blank=True, default="")
    domain = models.CharField(max_length=255, unique=True)
    business_type = models.CharField(
        max_length=20,
        choices=MerchantType.choices,
        default=MerchantType.INDEPENDENT,
    )
    auto_managed = models.BooleanField(
        default=False,
        help_text=(
            "Indicates whether this record is automatically managed based on "
            "merchant account data."
        ),
    )

    class Meta:
        ordering = ["account_name", "domain"]

    def __str__(self) -> str:
        name = self.display_name
        type_display = self.get_business_type_display()
        return f"{name} – {self.domain} ({type_display})"

    @property
    def display_name(self) -> str:
        if self.account:
            full_name = (self.account.get_full_name() or "").strip()
            if full_name:
                return full_name
            return self.account.get_username()
        return self.account_name or self.domain

    def save(self, *args, **kwargs):
        if self.account and not self.account_name:
            full_name = (self.account.get_full_name() or "").strip()
            self.account_name = full_name or self.account.get_username()
        if not self.account_name:
            self.account_name = self.domain
        super().save(*args, **kwargs)


class Config(models.Model):
    merchant_version = models.IntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Merchant Configuration"
        verbose_name_plural = "Merchant Configuration"

    def __str__(self) -> str:
        return f"Merchant Config v{self.merchant_version}"
