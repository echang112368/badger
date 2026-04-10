from django.conf import settings
from django.db import models
from django.utils import timezone


class IntegrationProvider(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    YOUTUBE = "youtube", "YouTube"
    TIKTOK = "tiktok", "TikTok"
    TWITCH = "twitch", "Twitch"


class ConnectionStatus(models.TextChoices):
    CONNECTED = "connected", "Connected"
    EXPIRED = "expired", "Expired"
    ERROR = "error", "Error"
    DISCONNECTED = "disconnected", "Disconnected"


class SyncStatus(models.TextChoices):
    IDLE = "idle", "Idle"
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class SocialAccount(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_accounts",
    )
    provider = models.CharField(max_length=30, choices=IntegrationProvider.choices)
    external_account_id = models.CharField(max_length=255)
    username = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    profile_url = models.URLField(blank=True)
    profile_picture_url = models.URLField(blank=True)
    scopes = models.JSONField(default=list, blank=True)
    account_metadata = models.JSONField(default=dict, blank=True)
    connection_status = models.CharField(
        max_length=20,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.CONNECTED,
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_sync_status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.IDLE,
    )
    last_error = models.TextField(blank=True)
    connected_at = models.DateTimeField(default=timezone.now)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "provider")
        indexes = [models.Index(fields=["provider", "connection_status"]) ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.provider}:{self.username or self.external_account_id}"


class SocialAccountToken(models.Model):
    social_account = models.OneToOneField(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="token",
    )
    access_token_encrypted = models.TextField()
    refresh_token_encrypted = models.TextField(blank=True)
    token_type = models.CharField(max_length=40, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    token_metadata = models.JSONField(default=dict, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class OAuthState(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_states",
    )
    provider = models.CharField(max_length=30, choices=IntegrationProvider.choices)
    state = models.CharField(max_length=255, unique=True)
    redirect_path = models.CharField(max_length=255, default="/creators/settings/")
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_valid(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()


class IntegrationSyncRun(models.Model):
    social_account = models.ForeignKey(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="sync_runs",
    )
    status = models.CharField(max_length=20, choices=SyncStatus.choices, default=SyncStatus.QUEUED)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SocialMetricSnapshot(models.Model):
    social_account = models.ForeignKey(
        SocialAccount,
        on_delete=models.CASCADE,
        related_name="metric_snapshots",
    )
    provider = models.CharField(max_length=30, choices=IntegrationProvider.choices)
    captured_at = models.DateTimeField(default=timezone.now)
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-captured_at"]
