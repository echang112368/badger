import re

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.text import slugify

from shopify_app.oauth import normalise_shop_domain

from .models import (
    CompanyCreatorPreferences,
    ItemGroup,
    MerchantItem,
    MerchantMeta,
    MerchantTeamMember,
)


class TeamMemberCreateForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    role = forms.ChoiceField(
        choices=[
            (MerchantTeamMember.Role.ADMIN, "Admin"),
            (MerchantTeamMember.Role.MEMBER, "Member"),
            (MerchantTeamMember.Role.VIEWER, "Viewer"),
        ]
    )

    def generate_username(self, merchant):
        base = slugify(f"{self.cleaned_data['first_name']} {self.cleaned_data['last_name']}") or "team"
        username = base
        counter = 1
        from accounts.models import CustomUser

        while CustomUser.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}-{counter}"
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        from accounts.models import CustomUser

        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class TeamMemberUpdateForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    role = forms.ChoiceField(
        choices=[
            (MerchantTeamMember.Role.ADMIN, "Admin"),
            (MerchantTeamMember.Role.MEMBER, "Member"),
            (MerchantTeamMember.Role.VIEWER, "Viewer"),
        ]
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        from accounts.models import CustomUser

        qs = CustomUser.objects.filter(email__iexact=email)
        if self.user is not None:
            qs = qs.exclude(pk=self.user.pk)
        if qs.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class MerchantItemForm(forms.ModelForm):
    class Meta:
        model = MerchantItem
        fields = ["title", "link"]


SHOPIFY_DOMAIN_ERROR = "Enter a valid Shopify store URL ending in .myshopify.com."


def normalize_shopify_store_domain(value: str) -> str:
    """Return a validated, normalised Shopify domain or raise ``ValidationError``."""

    candidate = (value or "").strip()
    if not candidate:
        return ""

    normalised = normalise_shop_domain(candidate)
    if not normalised:
        raise ValidationError(SHOPIFY_DOMAIN_ERROR)

    if "/" in normalised or " " in normalised:
        raise ValidationError(SHOPIFY_DOMAIN_ERROR)

    if not re.match(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.myshopify\.com$", normalised):
        raise ValidationError(SHOPIFY_DOMAIN_ERROR)

    return normalised


class MerchantSettingsForm(forms.ModelForm):

    class Meta:
        model = MerchantMeta
        fields = [
            "company_name",
            "paypal_email",
            "billing_plan",
            "shopify_store_domain",
            "business_type",
        ]
        labels = {
            "company_name": "Business Name",
            "paypal_email": "PayPal Email (for invoices)",
            "billing_plan": "Billing Plan",
            "shopify_store_domain": "Shopify URL",
            "business_type": "Business Type",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            business_type_field = self.fields.get("business_type")
            if business_type_field:
                business_type_field.disabled = True
                business_type_field.required = False
                business_type_field.help_text = (
                    "Your business type was selected during sign-up and cannot be changed."
                )

        if (
            self.instance
            and self.instance.pk
            and self.instance.business_type == MerchantMeta.BusinessType.SHOPIFY
            and self.instance.shopify_store_domain
        ):
            shopify_field = self.fields.get("shopify_store_domain")
            if shopify_field:
                shopify_field.disabled = True
                shopify_field.help_text = "Your Shopify store URL is locked after it is linked."

    def clean_shopify_store_domain(self):
        """Normalize the Shopify domain to its hostname."""
        if (
            self.instance
            and self.instance.pk
            and self.instance.business_type == MerchantMeta.BusinessType.SHOPIFY
            and self.instance.shopify_store_domain
        ):
            return self.instance.shopify_store_domain

        domain = self.cleaned_data.get("shopify_store_domain", "")
        return normalize_shopify_store_domain(domain)

    def clean(self):
        cleaned = super().clean()
        business_type = cleaned.get("business_type") or MerchantMeta.BusinessType.INDEPENDENT
        paypal_email = (cleaned.get("paypal_email") or "").strip()
        shopify_domain = cleaned.get("shopify_store_domain") or ""

        if business_type == MerchantMeta.BusinessType.INDEPENDENT and not paypal_email:
            self.add_error(
                "paypal_email",
                "PayPal email is required for independent merchants.",
            )

        if (
            business_type == MerchantMeta.BusinessType.SHOPIFY
            and not (self.instance.shopify_store_domain or shopify_domain)
        ):
            self.add_error(
                "shopify_store_domain",
                "Shopify store URL is required for Shopify businesses.",
            )

        return cleaned

    def clean_business_type(self):
        if self.instance and self.instance.pk:
            return self.instance.business_type
        return self.cleaned_data.get("business_type") or MerchantMeta.BusinessType.INDEPENDENT


class ItemGroupForm(forms.ModelForm):
    items = forms.ModelMultipleChoiceField(
        queryset=MerchantItem.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    affiliate_percent = forms.DecimalField(
        required=True,
        min_value=0,
        max_value=100,
        label="Affiliate Percentage (%)",
    )

    return_policy_days = forms.IntegerField(
        required=True,
        min_value=0,
        label="Return policy (days)",
    )

    class Meta:
        model = ItemGroup
        fields = ["name", "items", "affiliate_percent", "return_policy_days"]

    def __init__(self, *args, merchant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if merchant is not None:
            qs = MerchantItem.objects.filter(merchant=merchant)
            if self.instance.pk:
                qs = qs.filter(Q(groups__isnull=True) | Q(groups=self.instance))
            else:
                qs = qs.filter(groups__isnull=True)
            self.fields["items"].queryset = qs

    def clean_items(self):
        items = self.cleaned_data.get("items")
        if not items:
            return items
        conflict = ItemGroup.objects.filter(items__in=items)
        if self.instance.pk:
            conflict = conflict.exclude(pk=self.instance.pk)
        if conflict.exists():
            raise forms.ValidationError(
                "Some selected items already belong to another group."
            )
        return items


class CompanyCreatorPreferencesForm(forms.ModelForm):
    CREATOR_STYLE_CHOICES = [
        ("educational", "Educational"),
        ("lifestyle", "Lifestyle"),
        ("comedic", "Comedic"),
        ("aesthetic", "Aesthetic"),
        ("review_testimonial", "Review / testimonial"),
        ("storytelling", "Storytelling"),
        ("technical_expert", "Technical expert"),
    ]
    CONTENT_DELIVERABLE_CHOICES = [
        ("reels", "Reels"),
        ("feed_posts", "Feed posts"),
        ("stories", "Stories"),
        ("ugc_only", "UGC only"),
        ("product_reviews", "Product reviews"),
        ("tutorials", "Tutorials"),
    ]

    preferred_creator_style = forms.MultipleChoiceField(
        choices=CREATOR_STYLE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    content_deliverables = forms.MultipleChoiceField(
        choices=CONTENT_DELIVERABLE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = CompanyCreatorPreferences
        fields = [
            "campaign_goal",
            "campaign_stage",
            "preferred_creator_style",
            "brand_tone",
            "content_deliverables",
            "performance_priority",
            "risk_tolerance",
            "budget_range",
            "ideal_creator_description",
            "brand_description",
            "product_or_service_description",
            "campaign_success_definition",
            "content_to_avoid",
            "competitor_or_conflict_notes",
            "example_creators_or_brands",
        ]
        widgets = {
            "campaign_goal": forms.Select(attrs={"class": "form-select"}),
            "campaign_stage": forms.Select(attrs={"class": "form-select"}),
            "brand_tone": forms.Select(attrs={"class": "form-select"}),
            "performance_priority": forms.Select(attrs={"class": "form-select"}),
            "risk_tolerance": forms.Select(attrs={"class": "form-select"}),
            "budget_range": forms.Select(attrs={"class": "form-select"}),
            "ideal_creator_description": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "brand_description": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "product_or_service_description": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "campaign_success_definition": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "content_to_avoid": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "competitor_or_conflict_notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "example_creators_or_brands": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "campaign_goal",
            "campaign_stage",
            "brand_tone",
            "performance_priority",
            "risk_tolerance",
            "budget_range",
        ):
            self.fields[name].required = False
            self.fields[name].choices = [("", "No preference")] + list(self.fields[name].choices)
