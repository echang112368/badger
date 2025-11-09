from django import forms
from django.contrib.auth import get_user_model


User = get_user_model()


class ShopifyOAuthSignupForm(forms.Form):
    """Collect account details for merchants completing Shopify OAuth."""

    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()
    company_name = forms.CharField(max_length=255, required=False)
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists. Choose login instead.")
        return email

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned

    def _generate_username(self, email: str) -> str:
        base = email.split("@")[0] or "merchant"
        username = base
        counter = 1
        while User.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}{counter}"
        return username

    def save(self):
        cleaned = self.cleaned_data
        email = cleaned["email"]
        user = User.objects.create_user(
            username=self._generate_username(email),
            email=email,
            password=cleaned["password1"],
            first_name=cleaned.get("first_name", ""),
            last_name=cleaned.get("last_name", ""),
        )
        user.is_active = True
        user.is_merchant = True
        user.save(update_fields=["is_active", "is_merchant"])
        return user

    def get_company_name(self) -> str:
        return self.cleaned_data.get("company_name", "")
