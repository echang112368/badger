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

    class PlanType(models.TextChoices):
        BADGER_EXTENSION = "badger_extension", "Badger creator bundle"
        MERCHANT_ONLY = "merchant_only", "Merchant only"

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
    shopify_billing_status = models.CharField(max_length=32, blank=True)
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
    plan_type = models.CharField(
        max_length=32,
        choices=PlanType.choices,
        default=PlanType.BADGER_EXTENSION,
        help_text=(
            "Choose the billing plan for this merchant. The Badger bundle includes the "
            "automatic creator and the merchant-only plan does not."
        ),
    )

    PLAN_PRICING = {
        PlanType.BADGER_EXTENSION: Decimal("30.00"),
        PlanType.MERCHANT_ONLY: Decimal("80.00"),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_plan_type = self.plan_type

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

    def requires_shopify_oauth(self) -> bool:
        """Return ``True`` if the merchant still needs to complete Shopify OAuth."""

        if self.business_type != self.BusinessType.SHOPIFY:
            return False

        return not self.shopify_access_token or not self.shopify_store_domain

    def save(self, *args, **kwargs):
        plan_changed = self.pk is None or (
            getattr(self, "_original_plan_type", None) != self.plan_type
        )
        if plan_changed:
            self.monthly_fee = self.get_plan_price()
        super().save(*args, **kwargs)
        self._original_plan_type = self.plan_type

    def refresh_from_db(self, using=None, fields=None):
        super().refresh_from_db(using=using, fields=fields)
        self._original_plan_type = self.plan_type

    def get_plan_price(self) -> Decimal:
        return self.PLAN_PRICING.get(self.plan_type, Decimal("30.00"))

    @property
    def includes_badger_creator(self) -> bool:
        return self.plan_type == self.PlanType.BADGER_EXTENSION

    @property
    def display_monthly_fee(self) -> Decimal:
        fee = getattr(self, "monthly_fee", None)
        if fee is None:
            return self.get_plan_price()
        if isinstance(fee, Decimal):
            current_fee = fee
        else:
            current_fee = Decimal(fee)
        if current_fee <= 0:
            return self.get_plan_price()
        return current_fee

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
