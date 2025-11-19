from django import forms
from .models import MerchantCreatorLink
from accounts.models import CustomUser

class MerchantCreatorLinkForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['creator'].queryset = CustomUser.objects.filter(is_creator=True)

    class Meta:
        model = MerchantCreatorLink
        fields = ['creator', 'status']
