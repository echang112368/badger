from decimal import Decimal

from django.db import models
from django.db.models.functions import Lower
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
    shopify_billing_plan = models.CharField(max_length=32, blank=True)
    shopify_billing_status_updated_at = models.DateTimeField(null=True, blank=True)
    shopify_recurring_charge_id = models.CharField(max_length=64, blank=True)
    shopify_billing_confirmation_url = models.URLField(blank=True)
    shopify_usage_terms = models.CharField(max_length=255, blank=True)
    shopify_usage_capped_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
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
    def has_active_billing_plan(self) -> bool:
        """Return ``True`` when the merchant has selected and activated a plan."""

        if not self.billing_plan:
            return False

        if self.business_type == self.BusinessType.SHOPIFY:
            return (
                (self.shopify_billing_status or "").strip().lower() == "active"
                and (self.shopify_billing_plan or "") == self.billing_plan
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

    def requires_shopify_oauth(self) -> bool:
        """Return ``True`` if the merchant still needs to complete Shopify OAuth."""

        if self.business_type != self.BusinessType.SHOPIFY:
            return False

        return not self.shopify_access_token or not self.shopify_store_domain

class MerchantItem(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    link = models.URLField()
    shopify_product_id = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class ItemGroup(models.Model):
    merchant = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    items = models.ManyToManyField(MerchantItem, related_name="groups", blank=True)
    affiliate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    def __str__(self):
        return self.name
