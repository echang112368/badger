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
    marketplace_enabled = models.BooleanField(default=False)


    def __str__(self):
        return self.user.username

    def primary_platform_data(self):
        profiles = self.social_media_profiles or []
        platform = (self.social_media_platform or "").strip()
        follower_range = (self.follower_range or "").strip()
        avatar_url = ""
        if isinstance(profiles, list):
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                platform = (profile.get("platform") or platform).strip()
                follower_range = (profile.get("follower_range") or follower_range).strip()
                avatar_url = (profile.get("avatar_url") or avatar_url).strip()
                break
        return platform, follower_range, avatar_url

    @property
    def profile_completeness_score(self):
        platform, follower_range, _ = self.primary_platform_data()
        languages = [part.strip() for part in (self.content_languages or "").split(",") if part.strip()]
        skills = [skill for skill in (self.content_skills or []) if skill]
        fields = [
            bool(platform),
            bool(follower_range),
            bool(self.country.strip()) if self.country else False,
            bool(languages),
            bool(skills),
        ]
        if not fields:
            return 0
        return sum(fields) / len(fields)

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)


class SocialAnalyticsSnapshot(models.Model):
    PLATFORM_INSTAGRAM = "instagram"
    PLATFORM_TIKTOK = "tiktok"
    PLATFORM_YOUTUBE = "youtube"

    PLATFORM_CHOICES = (
        (PLATFORM_INSTAGRAM, "Instagram"),
        (PLATFORM_TIKTOK, "TikTok"),
        (PLATFORM_YOUTUBE, "YouTube"),
    )

    user = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="social_analytics_snapshots",
    )
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    payload = models.JSONField(default=dict, blank=True)
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "platform")

    def __str__(self):
        return f"{self.user.username} - {self.platform}"
