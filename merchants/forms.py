from django import forms
from .models import MerchantItem, MerchantMeta


class MerchantItemForm(forms.ModelForm):
    class Meta:
        model = MerchantItem
        fields = ["title", "link"]


class MerchantSettingsForm(forms.ModelForm):
    class Meta:
        model = MerchantMeta
        fields = ["paypal_email", "contact_email", "shopify_api_key", "shopify_api_password"]
        labels = {
            "paypal_email": "PayPal Email (for invoices)",
            "contact_email": "Business Email",
            "shopify_api_key": "API Key",
            "shopify_api_password": "API Password",
        }

