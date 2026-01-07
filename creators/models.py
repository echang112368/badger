from django.db import models
import uuid

class CreatorMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    paypal_email = models.EmailField(blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    display_name = models.CharField(max_length=120, blank=True)
    primary_platform = models.CharField(max_length=80, blank=True)
    primary_niche = models.CharField(max_length=120, blank=True)
    content_formats = models.CharField(max_length=200, blank=True)
    audience_locations = models.CharField(max_length=200, blank=True)
    audience_age_range = models.CharField(max_length=100, blank=True)
    preferred_partnerships = models.CharField(max_length=150, blank=True)
    average_engagement_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    media_kit_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    instagram_followers = models.PositiveIntegerField(null=True, blank=True)
    tiktok_url = models.URLField(blank=True)
    tiktok_followers = models.PositiveIntegerField(null=True, blank=True)
    youtube_url = models.URLField(blank=True)
    youtube_subscribers = models.PositiveIntegerField(null=True, blank=True)
    twitch_url = models.URLField(blank=True)
    twitch_followers = models.PositiveIntegerField(null=True, blank=True)


    def __str__(self):
        return self.user.username

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)
