from decimal import Decimal

from django.conf import settings
from django.db import models


class AffiliateClick(models.Model):
    """A single affiliate click between a creator and a merchant."""

    uuid = models.UUIDField(db_index=True)
    storeID = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["uuid", "storeID"])]

    def __str__(self):
        return f"{self.uuid} → {self.storeID} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class RedirectLink(models.Model):
    short_code = models.CharField(max_length=255, unique=True)
    destination_url = models.URLField()
    queryParam = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.short_code}, {self.destination_url}, {self.queryParam}"


class ReferralVisit(models.Model):
    """A single visit generated from a creator referral link."""

    creator_uuid = models.UUIDField(db_index=True)
    merchant_uuid = models.UUIDField(null=True, blank=True, db_index=True)

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referral_visits",
    )
    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_referral_visits",
    )

    merchant_domain = models.CharField(max_length=255, blank=True)
    landing_url = models.URLField(max_length=1024, blank=True)
    landing_path = models.CharField(max_length=512, blank=True)
    query_string = models.TextField(blank=True)
    query_params = models.JSONField(default=dict, blank=True)
    referrer = models.URLField(max_length=1024, blank=True)
    user_agent = models.TextField(blank=True)
    visitor_id = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["creator_uuid", "merchant_uuid"])]

    def __str__(self):
        merchant_ref = self.merchant_domain or self.merchant_uuid
        return f"{self.creator_uuid} → {merchant_ref} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


class ReferralConversion(models.Model):
    """A conversion (order) attributed to a creator for a merchant."""

    creator_uuid = models.UUIDField(db_index=True)
    merchant_uuid = models.UUIDField(db_index=True)

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referral_conversions",
    )
    merchant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_referral_conversions",
    )

    order_id = models.CharField(max_length=255, blank=True)
    order_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    commission_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["creator_uuid", "merchant_uuid"]),
            models.Index(fields=["creator", "merchant"]),
        ]

    def __str__(self):
        return (
            f"Conversion {self.order_id or 'unknown'} for {self.creator_uuid} → "
            f"{self.merchant_uuid}"
        )


class CreatorMerchantStatus(models.Model):
    """Stores creator-specific status overrides for a merchant relationship."""

    creator = models.ForeignKey(
        "creators.CreatorMeta",
        on_delete=models.CASCADE,
        related_name="merchant_statuses",
    )
    merchant = models.ForeignKey(
        "merchants.MerchantMeta",
        on_delete=models.CASCADE,
        related_name="creator_statuses",
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("creator", "merchant")
        verbose_name = "Creator merchant status"
        verbose_name_plural = "Creator merchant statuses"

    def __str__(self):
        state = "active" if self.is_active else "inactive"
        return f"{self.creator} → {self.merchant} ({state})"
