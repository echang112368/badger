"""Database models for the Shopify app integration."""

from django.db import models


class Shop(models.Model):
    """Store Shopify OAuth credentials for an installed merchant."""

    shop_domain = models.CharField(max_length=255, unique=True)
    access_token = models.CharField(max_length=255)

    def __str__(self) -> str:  # pragma: no cover - helpful for admin/debugging only
        return self.shop_domain
