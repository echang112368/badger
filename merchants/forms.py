from django import forms
from .models import MerchantItem, MerchantMeta


class MerchantItemForm(forms.ModelForm):
    class Meta:
        model = MerchantItem
        fields = ["title", "link"]


class MerchantMetaForm(forms.ModelForm):
    class Meta:
        model = MerchantMeta
        fields = ["affiliate_percent"]
        labels = {"affiliate_percent": "Commission Rate (%)"}

