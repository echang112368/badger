from django import forms

from .models import CreatorMeta


class CreatorSettingsForm(forms.ModelForm):
    shopify_access_token = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
    )

    class Meta:
        model = CreatorMeta
        fields = [
            "paypal_email",
            "shopify_access_token",
            "shopify_store_domain",
        ]
        labels = {
            "paypal_email": "PayPal Email",
            "shopify_access_token": "Access Token",
            "shopify_store_domain": "Shopify URL",
        }

    def clean_shopify_store_domain(self):
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
