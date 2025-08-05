from django import forms
from .models import MerchantItem, MerchantMeta


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
            "paypal_email",
            "shopify_access_token",
            "shopify_store_domain",
        ]
        labels = {
            "paypal_email": "PayPal Email (for invoices)",
            "shopify_access_token": "Access Token",
            "shopify_store_domain": "Shopify URL",
        }

