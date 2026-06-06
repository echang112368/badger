from django.db import models
import uuid


class PartnerMessage(models.Model):
    partnership = models.ForeignKey(
        "links.MerchantCreatorLink",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey("accounts.CustomUser", on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_opening_message = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message {self.id} for partnership {self.partnership_id}"

class CreatorMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    paypal_email = models.EmailField(blank=True)
    social_media_platform = models.CharField(max_length=100, blank=True)
    follower_range = models.CharField(max_length=50, blank=True)
    short_pitch = models.CharField(max_length=240, blank=True)
    social_media_profiles = models.JSONField(default=list, blank=True)
    content_skills = models.JSONField(default=list, blank=True)
    niches = models.JSONField(default=list, blank=True)
    country = models.CharField(max_length=100, blank=True)
    content_languages = models.CharField(max_length=200, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    marketplace_enabled = models.BooleanField(default=False)
    paid_brand_deals_count = models.PositiveIntegerField(default=0)
    gifted_brand_deals_count = models.PositiveIntegerField(default=0)
    affiliate_brand_deals_count = models.PositiveIntegerField(default=0)
    avg_sponsored_conversion_rate_pct = models.FloatField(default=0.0)
    partnership_history_notes = models.TextField(blank=True)


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

class GmailOAuthCredential(models.Model):
    """Stores the creator's server-side Gmail OAuth connection state."""

    STATUS_DISCONNECTED = "disconnected"
    STATUS_CONNECTED = "connected"
    STATUS_EXPIRED_REFRESHABLE = "expired_refreshable"
    STATUS_NEEDS_REAUTH = "needs_reauth"
    STATUS_REVOKED = "revoked"
    STATUS_ERROR = "error"
    STATUS_CHOICES = (
        (STATUS_DISCONNECTED, "Disconnected"),
        (STATUS_CONNECTED, "Connected"),
        (STATUS_EXPIRED_REFRESHABLE, "Expired but refreshable"),
        (STATUS_NEEDS_REAUTH, "Needs reauthorization"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_ERROR, "Error"),
    )

    user = models.OneToOneField(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="gmail_oauth_credential",
    )
    gmail_email = models.EmailField(blank=True)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_uri = models.CharField(
        max_length=255,
        default="https://oauth2.googleapis.com/token",
    )
    scopes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_DISCONNECTED,
    )
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Gmail OAuth credential"
        verbose_name_plural = "Gmail OAuth credentials"

    def __str__(self):
        email = self.gmail_email or "unconfirmed Gmail account"
        return f"{email} for {self.user}"


class OutreachDraft(models.Model):
    """Creator-reviewed outreach email prepared for Gmail draft/send flows."""

    STATUS_GENERATED = "generated"
    STATUS_EDITED = "edited"
    STATUS_GMAIL_DRAFTED = "gmail_drafted"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_GENERATED, "Generated"),
        (STATUS_EDITED, "Edited"),
        (STATUS_GMAIL_DRAFTED, "Gmail drafted"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    )

    creator = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="outreach_drafts",
    )
    business = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_outreach_drafts",
    )
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    tone = models.CharField(max_length=32, blank=True)
    gmail_draft_id = models.CharField(max_length=255, blank=True)
    gmail_thread_id = models.CharField(max_length=255, blank=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_GENERATED)
    last_agent_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Outreach draft {self.id} to {self.recipient_email}"


class OutreachThreadSummary(models.Model):
    creator = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="outreach_thread_summaries",
    )
    business = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_outreach_thread_summaries",
    )
    gmail_thread_id = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    next_actions = models.JSONField(default=list, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("creator", "gmail_thread_id")
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Thread summary {self.gmail_thread_id} for {self.creator}"


class OutreachAgentInteraction(models.Model):
    ACTION_GENERATE = "generate"
    ACTION_REVISE = "revise"
    ACTION_SUMMARIZE_THREAD = "summarize_thread"
    ACTION_SUGGEST_REPLY = "suggest_reply"
    ACTION_NEXT_ACTIONS = "next_actions"
    ACTION_CHOICES = (
        (ACTION_GENERATE, "Generate"),
        (ACTION_REVISE, "Revise"),
        (ACTION_SUMMARIZE_THREAD, "Summarize thread"),
        (ACTION_SUGGEST_REPLY, "Suggest reply"),
        (ACTION_NEXT_ACTIONS, "Next actions"),
    )

    creator = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.CASCADE,
        related_name="outreach_agent_interactions",
    )
    business = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_outreach_agent_interactions",
    )
    action_type = models.CharField(max_length=32, choices=ACTION_CHOICES)
    safe_input = models.JSONField(default=dict, blank=True)
    structured_output = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action_type} interaction for {self.creator}"
