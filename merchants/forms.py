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

