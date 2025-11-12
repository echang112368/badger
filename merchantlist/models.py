from django.db import models


class Merchant(models.Model):
    domain = models.CharField(max_length=255, unique=True)

    class Meta:
        ordering = ["domain"]

    def __str__(self) -> str:
        return self.domain


class Config(models.Model):
    merchant_version = models.IntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Merchant Configuration"
        verbose_name_plural = "Merchant Configuration"

    def __str__(self) -> str:
        return f"Merchant Config v{self.merchant_version}"
