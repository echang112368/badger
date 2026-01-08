from django.db import models
import uuid

class CreatorMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    paypal_email = models.EmailField(blank=True)
    display_name = models.CharField(max_length=150, blank=True)
    primary_platform = models.CharField(max_length=100, blank=True)
    social_platforms = models.TextField(blank=True)
    social_links = models.TextField(blank=True)
    follower_count = models.PositiveIntegerField(null=True, blank=True)
    engagement_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    average_views_per_post = models.PositiveIntegerField(null=True, blank=True)
    audience_location = models.CharField(max_length=200, blank=True)
    audience_age_range = models.CharField(max_length=100, blank=True)
    content_categories = models.TextField(blank=True)
    content_formats = models.TextField(blank=True)
    posting_frequency = models.CharField(max_length=100, blank=True)
    collaboration_types = models.TextField(blank=True)
    preferred_commission_range = models.CharField(max_length=100, blank=True)
    brand_affinities = models.TextField(blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)


    def __str__(self):
        return self.user.username

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)
