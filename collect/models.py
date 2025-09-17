from django.conf import settings
from django.db import models


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
