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


class MerchantItemForm(forms.ModelForm):
    class Meta:
        model = MerchantItem
        fields = ["title", "link"]


class MerchantSettingsForm(forms.ModelForm):
    shopify_access_token = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
    )

    class Meta:
        model = MerchantMeta
        fields = [
            "company_name",
            "paypal_email",
            "shopify_access_token",
            "shopify_store_domain",
        ]
        labels = {
            "company_name": "Business Name",
            "paypal_email": "PayPal Email (for invoices)",
            "shopify_access_token": "Access Token",
            "shopify_store_domain": "Shopify URL",
        }

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

