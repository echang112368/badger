from django.conf import settings
from django.db import models
from django.utils import timezone


class YouTubeConnection(models.Model):
    PLATFORM_YOUTUBE = "youtube"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="youtube_connection",
    )
    platform = models.CharField(max_length=32, default=PLATFORM_YOUTUBE)
    youtube_channel_id = models.CharField(max_length=100, unique=True)
    youtube_channel_title = models.CharField(max_length=255, blank=True)
    youtube_channel_handle = models.CharField(max_length=255, blank=True)
    youtube_custom_url = models.CharField(max_length=255, blank=True)
    subscribers_count = models.IntegerField(default=0)
    video_count = models.IntegerField(default=0)
    view_count = models.BigIntegerField(default=0)
    youtube_access_token = models.TextField(blank=True)
    youtube_refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    connected_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    raw_profile_data = models.JSONField(default=dict, blank=True)
    raw_channel_statistics = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        channel = self.youtube_channel_handle or self.youtube_channel_title or self.youtube_channel_id
        return f"{self.user} -> {channel}"
