from django import forms
from .models import ShopifyCredential


class ShopifySettingsForm(forms.ModelForm):
    api_key = forms.CharField(label="Shopify API Key", max_length=255)
    api_password = forms.CharField(label="Shopify API Password", max_length=255, widget=forms.PasswordInput)

    class Meta:
        model = ShopifyCredential
        fields = ["api_key", "api_password"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["api_key"].initial = self.instance.get_api_key()
            self.fields["api_password"].initial = self.instance.get_api_password()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.set_api_key(self.cleaned_data.get("api_key", ""))
        instance.set_api_password(self.cleaned_data.get("api_password", ""))
        if commit:
            instance.save()
        return instance
