from django import forms
from .models import MerchantItem, MerchantMeta


class MerchantItemForm(forms.ModelForm):
    class Meta:
        model = MerchantItem
        fields = ["title", "link"]


class MerchantSettingsForm(forms.ModelForm):
    shopify_api_password = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
    )
    class Meta:
        model = MerchantMeta
        fields = [
            "paypal_email",
            "shopify_api_key",
            "shopify_api_password",
        ]
        labels = {
            "paypal_email": "PayPal Email (for invoices)",
            "shopify_api_key": "Key",
            "shopify_api_password": "Password",
        }

