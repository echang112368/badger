import logging

from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.forms import UserCreationForm
from merchants.models import MerchantMeta
from merchants.forms import normalize_shopify_store_domain


User = get_user_model()
logger = logging.getLogger(__name__)


class CustomLoginForm(AuthenticationForm):
    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "you@example.com",
            }
        ),
    )
    password = forms.CharField(widget=forms.PasswordInput)

    def clean(self):
        email = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if email and password:
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                raise self.get_invalid_login_error()

            self.user_cache = authenticate(
                self.request,
                username=user.get_username(),
                password=password,
            )

            if self.user_cache is None:
                raise self.get_invalid_login_error()

            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

class BusinessSignUpForm(UserCreationForm):
    business_type = forms.ChoiceField(
        label="Business Type",
        choices=MerchantMeta.BusinessType.choices,
        initial=MerchantMeta.BusinessType.INDEPENDENT,
        widget=forms.RadioSelect,
        help_text="This selection determines how you'll be billed and cannot be changed later.",
    )
    shopify_store_domain = forms.CharField(
        label="Shopify store URL",
        required=False,
        help_text="Enter the myshopify.com URL for your store.",
        widget=forms.TextInput(
            attrs={"placeholder": "mystore.myshopify.com", "inputmode": "url"}
        ),
    )

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "password1",
            "password2",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "username" in self.fields:
            self.fields["username"].label = "Business name"

    def clean(self):
        cleaned = super().clean()
        business_type = cleaned.get("business_type")
        shopify_domain = cleaned.get("shopify_store_domain") or ""

        if business_type == MerchantMeta.BusinessType.SHOPIFY:
            try:
                cleaned["shopify_store_domain"] = normalize_shopify_store_domain(shopify_domain)
            except forms.ValidationError as exc:
                self.add_error("shopify_store_domain", exc)
            if not shopify_domain:
                self.add_error(
                    "shopify_store_domain",
                    "Shopify store URL is required for Shopify businesses.",
                )
        else:
            cleaned["shopify_store_domain"] = ""

        return cleaned

class CreatorSignUpForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "username" in self.fields:
            self.fields["username"].label = "Creator name"


class UserSignUpForm(UserCreationForm):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "you@example.com",
            }
        ),
    )

    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "password1", "password2")

    def save(self, commit=True):
        user = super().save(commit=False)
        email = self.cleaned_data.get("email", "").strip()
        user.email = email
        user.username = email
        if commit:
            user.save()
        return user

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if "@" not in email:
            raise forms.ValidationError("Enter a valid email address.")
        return email


class UserNameForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name")


class EmailVerificationForm(forms.Form):
    code = forms.CharField(
        label="Verification code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Enter 6-digit code",
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
            }
        ),
    )


class DevFriendlyPasswordResetForm(PasswordResetForm):
    """Log reset links in development so local testing is easier."""

    def get_users(self, email):
        """Log why a reset is skipped when an account matches but is ineligible."""

        normalized_email = email or ""
        active_and_usable = []
        matched_users = User._default_manager.filter(email__iexact=normalized_email)

        for user in matched_users:
            if user.is_active and user.has_usable_password():
                active_and_usable.append(user)
            elif settings.DEBUG:
                logger.warning(
                    "DEV password reset skipped for %s (user_id=%s, is_active=%s, usable_password=%s)",
                    normalized_email,
                    user.pk,
                    user.is_active,
                    user.has_usable_password(),
                )

        return active_and_usable

    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        super().send_mail(
            subject_template_name,
            email_template_name,
            context,
            from_email,
            to_email,
            html_email_template_name=html_email_template_name,
        )

        if settings.DEBUG:
            reset_url = (
                f"{context.get('protocol', 'http')}://{context.get('domain', '')}"
                f"/accounts/reset/{context.get('uid', '')}/{context.get('token', '')}/"
            )
            logger.warning(
                "DEV password reset link for %s: %s",
                to_email,
                reset_url,
            )
