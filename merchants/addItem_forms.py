from django import forms
from .models import MerchantItem

class MerchantItemForm(forms.ModelForm):
    class Meta:
        model = MerchantItem
        fields = ['title', 'link']

