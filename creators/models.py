import uuid

from django.db import models
from django.utils import timezone

class CreatorMeta(models.Model):
    ONBOARDING_STEPS = [
        ("identity", "Identity"),
        ("platforms", "Platforms"),
        ("content", "Content"),
        ("performance", "Performance"),
        ("payouts", "Payouts"),
        ("complete", "Complete"),
    ]
    ONBOARDING_PERCENT = {
        "identity": 20,
        "platforms": 40,
        "content": 60,
        "performance": 80,
        "payouts": 100,
    }
    REQUIRED_STEPS = {"identity", "platforms", "payouts"}

    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    bio = models.TextField(blank=True)
    paypal_email = models.EmailField(blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    country = models.CharField(max_length=100, blank=True)
    primary_niches = models.JSONField(default=list, blank=True)
    platforms = models.JSONField(default=list, blank=True)
    content_style_tags = models.JSONField(default=list, blank=True)
    posting_frequency = models.CharField(max_length=80, blank=True)
    open_to_gifting = models.BooleanField(null=True, blank=True)
    payout_method = models.CharField(max_length=50, blank=True)
    tax_info = models.TextField(blank=True)
    onboarding_step = models.CharField(choices=ONBOARDING_STEPS, default="identity")
    onboarding_completed = models.BooleanField(default=False)
    onboarding_completion_percent = models.IntegerField(default=0)
    onboarding_started_at = models.DateTimeField(null=True, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    onboarding_skipped_steps = models.JSONField(default=list, blank=True)


    def __str__(self):
        return self.user.username

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.creator_balance(self.user)

    def _identity_complete(self):
        niches = [n for n in (self.primary_niches or []) if str(n).strip()]
        return bool(self.user.username and self.country and niches)

    def _platforms_complete(self):
        for entry in self.platforms or []:
            if entry.get("platform") and entry.get("url"):
                return True
        return False

    def _content_complete(self):
        return bool(self.content_style_tags or self.posting_frequency or self.open_to_gifting is not None)

    def _performance_complete(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.objects.filter(creator=self.user, entry_type="commission").exists()

    def _payouts_complete(self):
        if not self.payout_method:
            return False
        if self.payout_method == "paypal":
            return bool(self.paypal_email)
        return True

    def refresh_onboarding_progress(self, persist: bool = True):
        step_completion = {
            "identity": self._identity_complete(),
            "platforms": self._platforms_complete(),
            "content": self._content_complete(),
            "performance": self._performance_complete(),
            "payouts": self._payouts_complete(),
        }
        skipped = set(self.onboarding_skipped_steps or [])
        for step, completed in step_completion.items():
            if completed and step in skipped:
                skipped.remove(step)

        completion_percent = 0
        for step, percent in self.ONBOARDING_PERCENT.items():
            if step_completion.get(step):
                completion_percent = max(completion_percent, percent)
        if step_completion.get("payouts"):
            completion_percent = 100

        required_complete = all(step_completion[step] for step in self.REQUIRED_STEPS)
        onboarding_completed = required_complete

        next_step = "complete"
        for step, _label in self.ONBOARDING_STEPS:
            if step == "complete":
                continue
            if step in skipped and step not in self.REQUIRED_STEPS:
                continue
            if not step_completion.get(step):
                next_step = step
                break

        if onboarding_completed:
            next_step = "complete"
            completion_percent = 100

        self.onboarding_completion_percent = completion_percent
        self.onboarding_completed = onboarding_completed
        self.onboarding_step = next_step
        self.onboarding_skipped_steps = list(skipped)
        if onboarding_completed and self.onboarding_completed_at is None:
            self.onboarding_completed_at = timezone.now()

        if persist:
            self.save(
                update_fields=[
                    "onboarding_completion_percent",
                    "onboarding_completed",
                    "onboarding_step",
                    "onboarding_completed_at",
                    "onboarding_skipped_steps",
                ]
            )
        return step_completion, skipped

    def onboarding_status(self):
        step_completion, skipped = self.refresh_onboarding_progress(persist=False)
        steps = []
        for step, label in self.ONBOARDING_STEPS:
            if step == "complete":
                continue
            steps.append(
                {
                    "step": step,
                    "label": label,
                    "completed": bool(step_completion.get(step)),
                    "skipped": step in skipped,
                    "required": step in self.REQUIRED_STEPS,
                }
            )
        performance_summary = None
        if step_completion.get("performance"):
            from django.db.models import Sum, Count
            from ledger.models import LedgerEntry

            summary = LedgerEntry.objects.filter(
                creator=self.user, entry_type="commission"
            ).aggregate(total=Sum("amount"), count=Count("id"))
            performance_summary = {
                "total_earnings": float(summary["total"] or 0),
                "sales_count": summary["count"] or 0,
            }

        return {
            "current_step": self.onboarding_step,
            "completion_percent": self.onboarding_completion_percent,
            "onboarding_completed": self.onboarding_completed,
            "next_recommended_step": self.onboarding_step,
            "steps": steps,
            "performance_summary": performance_summary,
            "profile": {
                "display_name": self.user.username,
                "country": self.country,
                "primary_niches": self.primary_niches,
                "platforms": self.platforms,
                "content_style_tags": self.content_style_tags,
                "posting_frequency": self.posting_frequency,
                "open_to_gifting": self.open_to_gifting,
                "payout_method": self.payout_method,
                "paypal_email": self.paypal_email,
                "tax_info": self.tax_info,
            },
        }
