from django.conf import settings
from django.db import models
from django.utils import timezone


class InstagramConnection(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="instagram_connection",
    )
    facebook_user_id = models.CharField(max_length=100, blank=True)
    page_id = models.CharField(max_length=100, blank=True)
    page_name = models.CharField(max_length=255, blank=True)
    instagram_user_id = models.CharField(max_length=100, unique=True)
    instagram_username = models.CharField(max_length=255, blank=True)
    followers_count = models.IntegerField(default=0)
    media_count = models.IntegerField(default=0)
    access_token = models.TextField()
    user_access_token = models.TextField(blank=True)
    page_access_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    connected_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        username = self.instagram_username or self.instagram_user_id
        return f"{self.user} -> @{username}"
