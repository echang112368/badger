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
        company_name_field = self.fields.get("company_name")
        if company_name_field:
            company_name_field.required = True
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

    def clean_company_name(self):
        company_name = (self.cleaned_data.get("company_name") or "").strip()
        if not company_name:
            raise forms.ValidationError("Business name is required.")
        return company_name

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
    PLATFORM_CHOICES = [
        ("instagram", "Instagram"),
        ("tiktok", "TikTok"),
        ("youtube", "YouTube"),
        ("facebook", "Facebook"),
        ("pinterest", "Pinterest"),
    ]
    SUCCESS_METRIC_CHOICES = [
        ("link_clicks", "Link clicks"),
        ("promo_code_redemptions", "Promo code redemptions"),
        ("follower_growth", "Follower growth"),
        ("content_saves", "Content saves"),
        ("brand_mentions", "Brand mentions"),
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
    preferred_platforms = forms.MultipleChoiceField(
        choices=PLATFORM_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    budget_min = forms.IntegerField(
        required=False,
        min_value=0,
        label="Budget minimum",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 1000"}),
    )
    budget_max = forms.IntegerField(
        required=False,
        min_value=0,
        label="Budget maximum",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 5000"}),
    )
    minimum_engagement_rate = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        label="Minimum engagement rate (%)",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "e.g. 3.00"}),
    )

    class Meta:
        model = CompanyCreatorPreferences
        fields = [
            "campaign_goal",
            "campaign_stage",
            "preferred_creator_style",
            "brand_tone",
            "brand_tone_keywords",
            "target_customer_age_range",
            "target_customer_gender_skew",
            "target_customer_location",
            "preferred_platforms",
            "content_deliverables",
            "performance_priority",
            "risk_tolerance",
            "budget_min",
            "budget_max",
            "minimum_engagement_rate",
            "success_metric_priority",
            "has_run_influencer_campaigns_before",
            "past_campaign_learnings",
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
            "performance_priority": forms.Select(attrs={"class": "form-select"}),
            "risk_tolerance": forms.Select(attrs={"class": "form-select"}),
            "brand_tone_keywords": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. warm, evidence-led, optimistic"}),
            "target_customer_age_range": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 25-40"}),
            "target_customer_gender_skew": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 70% women, 30% men"}),
            "target_customer_location": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. United States, urban metros"}),
            "success_metric_priority": forms.Select(attrs={"class": "form-select"}),
            "has_run_influencer_campaigns_before": forms.Select(attrs={"class": "form-select"}),
            "past_campaign_learnings": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "ideal_creator_description": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "brand_description": forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "e.g. We make clean skincare for busy moms who want effective products without harsh chemicals."}),
            "product_or_service_description": forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "e.g. Hero product is a fragrance-free vitamin C serum for sensitive skin."}),
            "campaign_success_definition": forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "e.g. 500 qualified clicks and 50 first-time purchases in 30 days."}),
            "content_to_avoid": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "competitor_or_conflict_notes": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "example_creators_or_brands": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in (
            "campaign_goal",
            "campaign_stage",
            "performance_priority",
            "risk_tolerance",
        ):
            self.fields[name].required = False
            self.fields[name].choices = [("", "No preference")] + list(self.fields[name].choices)
        self.fields["performance_priority"].choices[0] = ("", "No preference (we'll balance reach, engagement, and conversions)")
        self.fields["risk_tolerance"].choices = [
            ("", "No preference"),
            ("conservative_brand_safe", "Low risk (brand-safe, conservative content)"),
            ("balanced", "Balanced"),
            ("experimental_trend_driven", "High risk (edgier creators, bigger upside)"),
        ]
        self.fields["success_metric_priority"].choices = [("", "No preference")] + self.SUCCESS_METRIC_CHOICES
        self.fields["has_run_influencer_campaigns_before"].choices = [
            ("", "Select one"),
            ("True", "Yes"),
            ("False", "No"),
        ]

    def clean(self):
        cleaned_data = super().clean()
        budget_min = cleaned_data.get("budget_min")
        budget_max = cleaned_data.get("budget_max")

        if budget_min is not None and budget_max is not None and budget_min > budget_max:
            self.add_error("budget_max", "Maximum budget must be greater than or equal to minimum budget.")

        return cleaned_data
