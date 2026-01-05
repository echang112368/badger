from django.db import models
import uuid


class OnboardingStep(models.TextChoices):
    IDENTITY = "identity", "Identity"
    PLATFORMS = "platforms", "Platforms"
    CONTENT = "content", "Content"
    PERFORMANCE = "performance", "Performance"
    PAYOUTS = "payouts", "Payouts"
    COMPLETE = "complete", "Complete"

class CreatorMeta(models.Model):
    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    paypal_email = models.EmailField(blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    display_name = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=100, blank=True)
    primary_niches = models.JSONField(default=list, blank=True)
    platforms = models.JSONField(default=list, blank=True)
    content_style_tags = models.JSONField(default=list, blank=True)
    posting_frequency = models.CharField(max_length=120, blank=True)
    open_to_gifting = models.BooleanField(null=True, blank=True)
    payout_method = models.CharField(max_length=120, blank=True)
    tax_info_submitted = models.BooleanField(default=False)
    onboarding_step = models.CharField(
        max_length=32,
        choices=OnboardingStep.choices,
        default=OnboardingStep.IDENTITY,
    )
    onboarding_completed = models.BooleanField(default=False)
    onboarding_completion_percent = models.IntegerField(default=0)
    onboarding_content_skipped = models.BooleanField(default=False)
    onboarding_performance_skipped = models.BooleanField(default=False)


    def __str__(self):
        return self.user.username

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)

    def identity_complete(self):
        return bool(
            self.display_name
            and self.country
            and isinstance(self.primary_niches, list)
            and len(self.primary_niches) > 0
        )

    def platforms_complete(self):
        if not isinstance(self.platforms, list):
            return False
        for platform in self.platforms:
            if not isinstance(platform, dict):
                continue
            if platform.get("type") and platform.get("url"):
                return True
        return False

    def content_complete(self):
        if self.onboarding_content_skipped:
            return True
        if self.content_style_tags:
            return True
        if self.posting_frequency:
            return True
        return self.open_to_gifting is not None

    def performance_complete(self):
        return self.onboarding_performance_skipped

    def payouts_complete(self):
        if not self.payout_method or not self.tax_info_submitted:
            return False
        if self.payout_method.lower() == "paypal":
            return bool(self.paypal_email)
        return True

    def refresh_onboarding_status(self, save=True):
        completion_map = {
            OnboardingStep.IDENTITY: 20,
            OnboardingStep.PLATFORMS: 40,
            OnboardingStep.CONTENT: 60,
            OnboardingStep.PERFORMANCE: 80,
            OnboardingStep.PAYOUTS: 100,
        }

        if not self.identity_complete():
            next_step = OnboardingStep.IDENTITY
            percent = 0
        elif not self.platforms_complete():
            next_step = OnboardingStep.PLATFORMS
            percent = completion_map[OnboardingStep.IDENTITY]
        elif not self.content_complete():
            next_step = OnboardingStep.CONTENT
            percent = completion_map[OnboardingStep.PLATFORMS]
        elif not self.performance_complete():
            next_step = OnboardingStep.PERFORMANCE
            percent = completion_map[OnboardingStep.CONTENT]
        elif not self.payouts_complete():
            next_step = OnboardingStep.PAYOUTS
            percent = completion_map[OnboardingStep.PERFORMANCE]
        else:
            next_step = OnboardingStep.COMPLETE
            percent = completion_map[OnboardingStep.PAYOUTS]

        self.onboarding_step = next_step
        self.onboarding_completion_percent = percent
        self.onboarding_completed = next_step == OnboardingStep.COMPLETE

        if save:
            self.save(
                update_fields=[
                    "onboarding_step",
                    "onboarding_completion_percent",
                    "onboarding_completed",
                ]
            )

        return {
            "next_step": next_step,
            "completion_percent": percent,
            "onboarding_completed": self.onboarding_completed,
        }
