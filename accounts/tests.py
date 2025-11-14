from django.test import TestCase
from django.urls import reverse

from merchants.models import MerchantMeta

from .forms import UserSignUpForm
from .models import CustomUser


class SignUpFormTests(TestCase):
    def test_duplicate_email_not_allowed(self):
        CustomUser.objects.create_user(
            username="existing", email="dupe@example.com", password="pass123"
        )
        form = UserSignUpForm(
            data={
                "username": "newuser",
                "email": "dupe@example.com",
                "password1": "strongpass123",
                "password2": "strongpass123",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)


class LoginRedirectTests(TestCase):
    def test_shopify_merchant_without_oauth_redirects_to_authorize(self):
        user = CustomUser.objects.create_user(
            username="merchant_login",
            email="merchant_login@example.com",
            password="pass12345",
            is_merchant=True,
        )
        meta = MerchantMeta.objects.get(user=user)
        meta.business_type = MerchantMeta.BusinessType.SHOPIFY
        meta.shopify_store_domain = "https://Example.myshopify.com/"
        meta.shopify_access_token = ""
        meta.save()

        response = self.client.post(
            reverse("login"),
            {"username": user.email, "password": "pass12345"},
        )

        expected_url = (
            f"{reverse('shopify_oauth_authorize')}?shop=example.myshopify.com"
        )
        self.assertRedirects(
            response,
            expected_url,
            fetch_redirect_response=False,
        )
