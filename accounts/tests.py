from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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


class BusinessSignupTests(TestCase):
    def test_business_signup_sets_business_type(self):
        response = self.client.post(
            reverse("business_signup"),
            {
                "username": "merchant_signup",
                "first_name": "Merchant",
                "last_name": "User",
                "email": "merchant_signup@example.com",
                "password1": "strongpass123",
                "password2": "strongpass123",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "shopify_store_domain": "merchant-shop.myshopify.com",
            },
        )

        self.assertRedirects(
            response,
            reverse("verify_email"),
            fetch_redirect_response=False,
        )

        user = CustomUser.objects.get(username="merchant_signup")
        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.business_type, MerchantMeta.BusinessType.SHOPIFY)
        self.assertEqual(meta.shopify_store_domain, "merchant-shop.myshopify.com")

    def test_shopify_business_signup_requires_store_url(self):
        response = self.client.post(
            reverse("business_signup"),
            {
                "username": "shopify_missing_url",
                "first_name": "Shop",
                "last_name": "Owner",
                "email": "shopify_missing@example.com",
                "password1": "strongpass123",
                "password2": "strongpass123",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "shopify_store_domain": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertIn("shopify_store_domain", form.errors)
        self.assertFalse(
            CustomUser.objects.filter(username="shopify_missing_url").exists()
        )

    def test_shopify_business_signup_normalizes_store_url(self):
        response = self.client.post(
            reverse("business_signup"),
            {
                "username": "shopify_signup",
                "first_name": "Shopify",
                "last_name": "Owner",
                "email": "shopify_signup@example.com",
                "password1": "strongpass123",
                "password2": "strongpass123",
                "business_type": MerchantMeta.BusinessType.SHOPIFY,
                "shopify_store_domain": "https://Example.myshopify.com/",
            },
        )

        self.assertRedirects(
            response,
            reverse("verify_email"),
            fetch_redirect_response=False,
        )

        user = CustomUser.objects.get(username="shopify_signup")
        meta = MerchantMeta.objects.get(user=user)
        self.assertEqual(meta.shopify_store_domain, "example.myshopify.com")

    def test_business_signup_rejects_weak_password(self):
        response = self.client.post(
            reverse("business_signup"),
            {
                "username": "weakpassmerchant",
                "first_name": "Weak",
                "last_name": "Password",
                "email": "weak@example.com",
                "password1": "short",
                "password2": "short",
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)
        self.assertFalse(CustomUser.objects.filter(username="weakpassmerchant").exists())

    def test_business_signup_requires_matching_passwords(self):
        response = self.client.post(
            reverse("business_signup"),
            {
                "username": "mismatchmerchant",
                "first_name": "Mismatch",
                "last_name": "Password",
                "email": "mismatch@example.com",
                "password1": "strongpass123",
                "password2": "strongpass124",
                "business_type": MerchantMeta.BusinessType.INDEPENDENT,
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)
        self.assertFalse(CustomUser.objects.filter(username="mismatchmerchant").exists())


class LoginRedirectTests(TestCase):
    def test_shopify_merchant_without_oauth_redirects_to_authorize(self):
        user = CustomUser.objects.create_user(
            username="merchant_login",
            email="merchant_login@example.com",
            password="pass12345",
            is_merchant=True,
            email_verified=True,
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


class EmailVerificationFlowTests(TestCase):
    def test_login_requires_verification_when_last_login_stale(self):
        user = CustomUser.objects.create_user(
            username="stale_user",
            email="stale@example.com",
            password="pass12345",
            email_verified=False,
        )
        user.last_login = timezone.now() - timedelta(days=8)
        user.save(update_fields=["last_login"])

        response = self.client.post(
            reverse("login"),
            {"username": user.email, "password": "pass12345"},
        )

        self.assertRedirects(
            response,
            reverse("verify_email"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session.get("verification_user_id"), user.pk)

    def test_login_skips_verification_when_recently_active(self):
        user = CustomUser.objects.create_user(
            username="recent_user",
            email="recent@example.com",
            password="pass12345",
            email_verified=False,
        )
        user.last_login = timezone.now() - timedelta(days=1)
        user.save(update_fields=["last_login"])

        response = self.client.post(
            reverse("login"),
            {"username": user.email, "password": "pass12345"},
        )

        self.assertRedirects(
            response,
            reverse("user_dashboard"),
            fetch_redirect_response=False,
        )

    def test_middleware_blocks_stale_unverified_users(self):
        user = CustomUser.objects.create_user(
            username="stale_middleware",
            email="stale_middleware@example.com",
            password="pass12345",
            email_verified=False,
        )
        self.client.force_login(user)
        user.last_login = timezone.now() - timedelta(days=10)
        user.save(update_fields=["last_login"])
        response = self.client.get(reverse("user_dashboard"))

        self.assertRedirects(
            response,
            reverse("verify_email"),
            fetch_redirect_response=False,
        )

    def test_middleware_allows_recent_unverified_users(self):
        user = CustomUser.objects.create_user(
            username="recent_middleware",
            email="recent_middleware@example.com",
            password="pass12345",
            email_verified=False,
        )
        self.client.force_login(user)
        user.last_login = timezone.now() - timedelta(days=2)
        user.save(update_fields=["last_login"])
        response = self.client.get(reverse("user_dashboard"))

        self.assertEqual(response.status_code, 200)
