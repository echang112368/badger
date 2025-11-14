from decimal import Decimal

from django.db import models
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

    user = models.OneToOneField('accounts.CustomUser', on_delete=models.CASCADE)
    company_name = models.CharField(max_length=255, blank=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    paypal_email = models.EmailField(blank=True)
    shopify_access_token = models.CharField(max_length=255, blank=True)
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
