from django.db import models
import uuid

class CreatorMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    paypal_email = models.EmailField(blank=True)
    social_media_platform = models.CharField(max_length=100, blank=True)
    follower_range = models.CharField(max_length=50, blank=True)
    short_pitch = models.CharField(max_length=240, blank=True)
    social_media_profiles = models.JSONField(default=list, blank=True)
    content_skills = models.JSONField(default=list, blank=True)
    country = models.CharField(max_length=100, blank=True)
    content_languages = models.CharField(max_length=200, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)


    def __str__(self):
        return self.user.username

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)
