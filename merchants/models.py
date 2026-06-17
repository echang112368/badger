from decimal import Decimal
from typing import Optional

from django.db import models
from django.db.models.functions import Lower
from django.conf import settings
from django.utils import timezone
from accounts.models import CustomUser
import uuid


class MerchantTeamMember(models.Model):
    class Role(models.TextChoices):
        SUPERUSER = "superuser", "Superuser"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    merchant = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="team_memberships",
    )
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="merchant_team_membership",
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["merchant"],
                condition=models.Q(role="superuser"),
                name="unique_superuser_per_merchant",
            )
        ]

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"

class MerchantMeta(models.Model):
    class BusinessType(models.TextChoices):
        INDEPENDENT = "independent", "Independent"
        SHOPIFY = "shopify", "Shopify"

    class BillingPlan(models.TextChoices):
        PLATFORM_ONLY = "platform_only", "Platform only ($80/mo)"
        BADGER_CREATOR = "badger_creator", "Badger Creator included ($30/mo)"

    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    company_name = models.CharField(max_length=255, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    paypal_email = models.EmailField(blank=True)
    shopify_access_token = models.CharField(max_length=255, blank=True)
    shopify_refresh_token = models.CharField(max_length=255, blank=True)
    shopify_store_domain = models.CharField(max_length=255, blank=True)
    shopify_oauth_authorization_line = models.CharField(max_length=512, blank=True)
    business_type = models.CharField(
        max_length=20,
        choices=BusinessType.choices,
        default=BusinessType.INDEPENDENT,
    )
    billing_plan = models.CharField(
        max_length=32,
        choices=BillingPlan.choices,
        default=BillingPlan.BADGER_CREATOR,
        help_text=(
            "Choose between the platform-only plan and the plan that includes "
            "the automatic Badger creator."
        ),
    )
    shopify_billing_status = models.CharField(max_length=32, blank=True)
    shopify_billing_plan = models.CharField(max_length=64, blank=True)
    shopify_billing_verified_at = models.DateTimeField(null=True, blank=True)
    shopify_recurring_charge_id = models.CharField(max_length=64, blank=True)
    shopify_billing_confirmation_url = models.URLField(blank=True)
    shopify_usage_terms = models.CharField(max_length=255, blank=True)
    shopify_usage_capped_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    shopify_uninstalled_at = models.DateTimeField(null=True, blank=True)
    marketplace_enabled = models.BooleanField(default=False)
    monthly_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Recurring platform fee charged during each PayPal invoice cycle.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("shopify_store_domain"),
                condition=~models.Q(shopify_store_domain=""),
                name="unique_shopify_store_domain",
            )
        ]


    def __str__(self):
        return self.company_name

    @property
    def balance(self):
        from ledger.models import LedgerEntry
        return LedgerEntry.merchant_balance(self.user)

    @property
    def plan_price(self) -> Decimal:
        if self.billing_plan == self.BillingPlan.PLATFORM_ONLY:
            return Decimal("80.00")
        return Decimal("30.00")

    @property
    def includes_badger_creator(self) -> bool:
        return self.billing_plan == self.BillingPlan.BADGER_CREATOR

    @property
    def creator_limit(self) -> "Optional[int]":
        if self.billing_plan == self.BillingPlan.PLATFORM_ONLY:
            return 50
        return None

    @property
    def has_active_billing_plan(self) -> bool:
        """Return ``True`` when the merchant has selected and activated a plan."""

        if not self.billing_plan:
            return False

        if self.business_type == self.BusinessType.SHOPIFY:
            return (
                self.shopify_billing_status == "ACTIVE"
                and self.shopify_billing_plan == self.billing_plan
            )

        return self.monthly_fee > 0

    def ensure_badger_creator_link(self):
        """Ensure the default Badger creator link matches the selected plan."""

        try:
            from accounts.models import CustomUser
            from links.models import MerchantCreatorLink, STATUS_ACTIVE
            from creators.models import CreatorMeta
        except Exception:
            return

        default_creator = CustomUser.get_default_badger_creator()
        if not default_creator:
            return

        if self.includes_badger_creator:
            CreatorMeta.objects.get_or_create(user=default_creator)
            link, created = MerchantCreatorLink.objects.get_or_create(
                merchant=self.user,
                creator=default_creator,
                defaults={"status": STATUS_ACTIVE},
            )
            if not created and link.status != STATUS_ACTIVE:
                link.status = STATUS_ACTIVE
                link.save(update_fields=["status"])
        else:
            MerchantCreatorLink.objects.filter(
                merchant=self.user, creator=default_creator
            ).delete()

    def save(self, *args, **kwargs):
        if self.pk is None and (self.monthly_fee is None or self.monthly_fee == 0):
            self.monthly_fee = self.plan_price
        super().save(*args, **kwargs)
        self.ensure_badger_creator_link()

    def cancel_shopify_account(self, *, canceled_at=None) -> None:
        """Mark the Shopify merchant account as cancelled/uninstalled."""

        canceled_at = canceled_at or timezone.now()
        update_fields = []

        if self.shopify_uninstalled_at != canceled_at:
            self.shopify_uninstalled_at = canceled_at
            update_fields.append("shopify_uninstalled_at")

        if self.shopify_billing_status != "CANCELLED":
            self.shopify_billing_status = "CANCELLED"
            update_fields.append("shopify_billing_status")

        if self.shopify_billing_plan:
            self.shopify_billing_plan = ""
            update_fields.append("shopify_billing_plan")

        if self.shopify_billing_verified_at:
            self.shopify_billing_verified_at = None
            update_fields.append("shopify_billing_verified_at")

        if self.shopify_recurring_charge_id:
            self.shopify_recurring_charge_id = ""
            update_fields.append("shopify_recurring_charge_id")

        if self.shopify_billing_confirmation_url:
            self.shopify_billing_confirmation_url = ""
            update_fields.append("shopify_billing_confirmation_url")

        if self.shopify_usage_terms:
            self.shopify_usage_terms = ""
            update_fields.append("shopify_usage_terms")

        if self.shopify_usage_capped_amount is not None:
            self.shopify_usage_capped_amount = None
            update_fields.append("shopify_usage_capped_amount")

        if update_fields:
            self.save(update_fields=update_fields)

    @staticmethod
    def shopify_uninstall_grace_days() -> int:
        """Return the grace period before an uninstalled shop is fully released."""

        configured = getattr(settings, "SHOPIFY_UNINSTALL_GRACE_DAYS", 30)
        try:
            return max(int(configured), 0)
        except (TypeError, ValueError):
            return 30

    def shopify_uninstall_grace_expires_at(self):
        """Return when the uninstall grace period expires for this merchant."""

        if not self.shopify_uninstalled_at:
            return None
        return self.shopify_uninstalled_at + timezone.timedelta(
            days=self.shopify_uninstall_grace_days()
        )

    def is_shopify_uninstall_grace_expired(self, *, now=None) -> bool:
        """Return ``True`` if the uninstall grace period has elapsed."""

        expires_at = self.shopify_uninstall_grace_expires_at()
        if not expires_at:
            return False
        return (now or timezone.now()) >= expires_at

    def release_shopify_association(self) -> bool:
        """Detach Shopify store association and deactivate merchant access."""

        update_fields = []
        if self.shopify_store_domain:
            self.shopify_store_domain = ""
            update_fields.append("shopify_store_domain")

        if self.shopify_access_token:
            self.shopify_access_token = ""
            update_fields.append("shopify_access_token")

        if self.shopify_refresh_token:
            self.shopify_refresh_token = ""
            update_fields.append("shopify_refresh_token")

        if self.shopify_oauth_authorization_line:
            self.shopify_oauth_authorization_line = ""
            update_fields.append("shopify_oauth_authorization_line")

        if update_fields:
            self.save(update_fields=update_fields)

        user_fields = []
        if self.user.is_active:
            self.user.is_active = False
            user_fields.append("is_active")
        if self.user.is_merchant:
            self.user.is_merchant = False
            user_fields.append("is_merchant")
        if user_fields:
            self.user.save(update_fields=user_fields)

        return bool(update_fields or user_fields)

    def process_shopify_uninstall_grace_expiration(self, *, now=None) -> bool:
        """Release Shopify association once the uninstall grace period has passed."""

        if not self.shopify_uninstalled_at:
            return False
        if not self.is_shopify_uninstall_grace_expired(now=now):
            return False
        return self.release_shopify_association()

    def requires_shopify_oauth(self) -> bool:
        """Return ``True`` if the merchant still needs to complete Shopify OAuth."""

        if self.business_type != self.BusinessType.SHOPIFY:
            return False

        return not self.shopify_access_token or not self.shopify_store_domain


class CompanyCreatorPreferences(models.Model):
    class CampaignGoal(models.TextChoices):
        BRAND_AWARENESS = "brand_awareness", "Brand awareness"
        CONVERSIONS_SALES = "conversions_sales", "Conversions / sales"
        WEBSITE_TRAFFIC = "website_traffic", "Website traffic"
        UGC_CONTENT_CREATION = "ugc_content_creation", "UGC content creation"
        PRODUCT_LAUNCH = "product_launch", "Product launch"
        COMMUNITY_GROWTH = "community_growth", "Community growth"

    class CampaignStage(models.TextChoices):
        EXPLORING = "exploring", "Exploring (broader discovery)"
        READY_TO_CONTACT = "ready_to_contact", "Ready to contact (shortlist quality)"
        ACTIVE_CAMPAIGN = "active_campaign", "Active campaign (execution-focused)"

    class BrandTone(models.TextChoices):
        CASUAL = "casual", "Casual"
        PREMIUM = "premium", "Premium"
        PLAYFUL = "playful", "Playful"
        PROFESSIONAL = "professional", "Professional"
        SCIENCE_BACKED = "science_backed", "Science-backed"
        LUXURY = "luxury", "Luxury"


    class PerformancePriority(models.TextChoices):
        REACH = "reach", "Reach"
        ENGAGEMENT = "engagement", "Engagement"
        CONVERSIONS = "conversions", "Conversions"
        AUDIENCE_FIT = "audience_fit", "Audience fit"
        CONTENT_QUALITY = "content_quality", "Content quality"

    class RiskTolerance(models.TextChoices):
        CONSERVATIVE = "conservative_brand_safe", "Conservative / brand-safe"
        BALANCED = "balanced", "Balanced"
        EXPERIMENTAL = "experimental_trend_driven", "Experimental / trend-driven"

    class BudgetRange(models.TextChoices):
        UNDER_500 = "under_500", "Under $500"
        FROM_500_TO_1500 = "500_1500", "$500-$1,500"
        FROM_1500_TO_5000 = "1500_5000", "$1,500-$5,000"
        FROM_5000_PLUS = "5000_plus", "$5,000+"

    merchant = models.OneToOneField(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="creator_preferences",
    )
    campaign_goal = models.CharField(max_length=40, choices=CampaignGoal.choices, blank=True)
    campaign_stage = models.CharField(max_length=32, choices=CampaignStage.choices, blank=True)
    preferred_creator_style = models.JSONField(default=list, blank=True)
    brand_tone = models.CharField(max_length=32, choices=BrandTone.choices, blank=True)
    content_deliverables = models.JSONField(default=list, blank=True)
    performance_priority = models.CharField(
        max_length=32,
        choices=PerformancePriority.choices,
        blank=True,
    )
    risk_tolerance = models.CharField(max_length=40, choices=RiskTolerance.choices, blank=True)
    budget_range = models.CharField(max_length=32, choices=BudgetRange.choices, blank=True)
    budget_min = models.PositiveIntegerField(blank=True, null=True)
    budget_max = models.PositiveIntegerField(blank=True, null=True)
    brand_tone_keywords = models.CharField(max_length=120, blank=True)
    target_customer_age_range = models.CharField(max_length=50, blank=True)
    target_customer_gender_skew = models.CharField(max_length=50, blank=True)
    target_customer_location = models.CharField(max_length=120, blank=True)
    preferred_platforms = models.JSONField(default=list, blank=True)
    minimum_engagement_rate = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    success_metric_priority = models.CharField(max_length=40, blank=True)
    has_run_influencer_campaigns_before = models.BooleanField(blank=True, null=True)
    past_campaign_learnings = models.TextField(blank=True)
    ideal_creator_description = models.TextField(blank=True)
    brand_description = models.TextField(blank=True)
    product_or_service_description = models.TextField(blank=True)
    campaign_success_definition = models.TextField(blank=True)
    content_to_avoid = models.TextField(blank=True)
    competitor_or_conflict_notes = models.TextField(blank=True)
    example_creators_or_brands = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Company creator preferences"
        verbose_name_plural = "Company creator preferences"

    def __str__(self):
        return f"Creator preferences for {self.merchant.username}"

    @property
    def has_any_preferences(self) -> bool:
        values = [
            self.campaign_goal,
            self.campaign_stage,
            self.preferred_creator_style,
            self.brand_tone,
            self.content_deliverables,
            self.performance_priority,
            self.risk_tolerance,
            self.budget_range,
            self.budget_min,
            self.budget_max,
            self.brand_tone_keywords,
            self.target_customer_age_range,
            self.target_customer_gender_skew,
            self.target_customer_location,
            self.preferred_platforms,
            self.minimum_engagement_rate,
            self.success_metric_priority,
            self.has_run_influencer_campaigns_before,
            self.past_campaign_learnings,
            self.ideal_creator_description,
            self.brand_description,
            self.product_or_service_description,
            self.campaign_success_definition,
            self.content_to_avoid,
            self.competitor_or_conflict_notes,
            self.example_creators_or_brands,
        ]
        return any(value for value in values)

class MerchantItem(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    link = models.URLField()
    shopify_product_id = models.CharField(max_length=64, blank=True, null=True)
    image_url = models.URLField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class ItemGroup(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    items = models.ManyToManyField(MerchantItem, related_name="groups", blank=True)
    affiliate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    return_policy_days = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name
