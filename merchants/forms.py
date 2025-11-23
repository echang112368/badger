from django import forms
from django.db.models import Q
from .models import MerchantItem, MerchantMeta, ItemGroup, MerchantTeamMember
from django.utils.text import slugify


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


class MerchantSettingsForm(forms.ModelForm):
    shopify_oauth_authorization_line = forms.CharField(
        required=False,
        help_text="Optional header value used for custom integrations that rely on OAuth.",
    )

    class Meta:
        model = MerchantMeta
        fields = [
            "company_name",
            "paypal_email",
            "billing_plan",
            "shopify_store_domain",
            "shopify_oauth_authorization_line",
            "business_type",
        ]
        labels = {
            "company_name": "Business Name",
            "paypal_email": "PayPal Email (for invoices)",
            "billing_plan": "Billing Plan",
            "shopify_store_domain": "Shopify URL",
            "shopify_oauth_authorization_line": "OAuth Authorization Line",
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

    def clean_shopify_store_domain(self):
        """Normalize the Shopify domain to its hostname."""
        domain = self.cleaned_data.get("shopify_store_domain", "").strip()
        if not domain:
            return domain

        from urllib.parse import urlparse

        parsed = urlparse(domain if "://" in domain else f"//{domain}")
        host = parsed.netloc or parsed.path
        host = host.lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def clean(self):
        cleaned = super().clean()
        business_type = cleaned.get("business_type") or MerchantMeta.BusinessType.INDEPENDENT
        paypal_email = (cleaned.get("paypal_email") or "").strip()

        if business_type == MerchantMeta.BusinessType.INDEPENDENT and not paypal_email:
            self.add_error(
                "paypal_email",
                "PayPal email is required for independent merchants.",
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

    class Meta:
        model = ItemGroup
        fields = ["name", "items", "affiliate_percent"]

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

