from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser
from merchants.models import MerchantMeta
from decimal import Decimal
from ledger.models import LedgerEntry
from rest_framework_simplejwt.tokens import RefreshToken
from .models import CustomerMeta


class CustomerSettingsTests(TestCase):
    def test_settings_displays_uuid(self):
        user = CustomUser.objects.create_user(
            username='tester', password='pass123', email='uuidtester@example.com'
        )
        self.client.login(username='tester', password='pass123')
        response = self.client.get(reverse('user_settings'))
        self.assertEqual(response.status_code, 200)
        meta = CustomerMeta.objects.get(user=user)
        self.assertContains(response, str(meta.uuid))

    def test_settings_displays_email(self):
        user = CustomUser.objects.create_user(
            username='tester', password='pass123', email='tester@example.com'
        )
        self.client.login(username='tester', password='pass123')
        response = self.client.get(reverse('user_settings'))
        self.assertContains(response, user.email)

    def test_settings_does_not_display_password_hash(self):
        user = CustomUser.objects.create_user(
            username='tester2', password='secret', email='tester2@example.com'
        )
        self.client.login(username='tester2', password='secret')
        response = self.client.get(reverse('user_settings'))
        self.assertNotContains(response, user.password)

    def test_settings_updates_name(self):
        user = CustomUser.objects.create_user(
            username='tester3', password='pass123', email='tester3@example.com'
        )
        self.client.login(username='tester3', password='pass123')
        response = self.client.post(
            reverse('user_settings'),
            {'first_name': 'New', 'last_name': 'Name'},
        )
        self.assertRedirects(response, reverse('user_settings'))
        user.refresh_from_db()
        self.assertEqual(user.first_name, 'New')


class LoginAPITests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="tester",
            password="pass123",
            email="tester@example.com",
            first_name="Test",
            last_name="User",
        )

    def test_valid_login_returns_tokens_uuid_name_points(self):
        LedgerEntry.objects.create(
            creator=self.user,
            amount=Decimal("73"),
            entry_type="points",
        )
        LedgerEntry.objects.create(
            creator=self.user,
            amount=Decimal("9"),
            entry_type=LedgerEntry.EntryType.SAVINGS,
        )
        url = reverse("api_login")
        response = self.client.post(url, {
            "username": "tester@example.com",
            "password": "pass123",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertIn("uuid", data)
        self.assertIn("name", data)
        self.assertEqual(data["name"], "Test User")
        self.assertIn("points", data)
        self.assertEqual(data["points"], 73)
        self.assertIn("savings", data)
        self.assertEqual(data["savings"], 9)

    def test_invalid_login_returns_400(self):
        url = reverse("api_login")
        response = self.client.post(url, {
            "username": "tester@example.com",
            "password": "wrong",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "Invalid credentials")

    def test_merchant_login_returns_400(self):
        CustomUser.objects.create_user(
            username="merchant",
            password="pass123",
            email="merchant@example.com",
            is_merchant=True,
        )
        url = reverse("api_login")
        response = self.client.post(
            url,
            {
                "username": "merchant@example.com",
                "password": "pass123",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "Invalid credentials")

    def test_creator_login_returns_400(self):
        CustomUser.objects.create_user(
            username="creator",
            password="pass123",
            email="creator@example.com",
            is_creator=True,
        )
        url = reverse("api_login")
        response = self.client.post(
            url,
            {
                "username": "creator@example.com",
                "password": "pass123",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "Invalid credentials")


class DashboardViewTests(TestCase):
    def test_rewards_history_displays_company_and_points(self):
        merchant = CustomUser.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="pass123",
            is_merchant=True,
        )
        merchant_meta = MerchantMeta.objects.get(user=merchant)
        merchant_meta.company_name = "StoreCo"
        merchant_meta.save()

        customer = CustomUser.objects.create_user(
            username="customer",
            email="customer@example.com",
            password="pass123",
        )
        LedgerEntry.objects.create(
            creator=customer,
            merchant=merchant,
            amount=Decimal("120"),
            entry_type="points",
        )

        self.client.login(username="customer", password="pass123")
        response = self.client.get(reverse("user_dashboard"))
        self.assertContains(response, "StoreCo")
        self.assertContains(response, "+120 pts")
        self.assertContains(response, "+$2.00")


class CustomerPointsAPITests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="customer",
            password="secret",
            email="customer@example.com",
            first_name="Customer",
            last_name="User",
        )
        self.meta = CustomerMeta.objects.get(user=self.user)
        LedgerEntry.objects.create(
            creator=self.user,
            amount=Decimal("42"),
            entry_type="points",
        )
        LedgerEntry.objects.create(
            creator=self.user,
            amount=Decimal("15"),
            entry_type=LedgerEntry.EntryType.SAVINGS,
        )
        login_response = self.client.post(
            reverse("api_login"),
            {"username": "customer@example.com", "password": "secret"},
        )
        self.assertEqual(login_response.status_code, 200)
        self.login_tokens = login_response.json()

    def test_returns_points_and_new_tokens(self):
        url = reverse("api_points")
        response = self.client.post(
            url,
            {
                "uuid": str(self.meta.uuid),
                "refresh": self.login_tokens["refresh"],
                "access": self.login_tokens["access"],
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["uuid"], str(self.meta.uuid))
        self.assertEqual(data["points"], 42)
        self.assertEqual(data["savings"], 15)
        self.assertIn("access", data)
        self.assertIn("refresh", data)
        self.assertNotEqual(data["access"], self.login_tokens["access"])
        self.assertNotEqual(data["refresh"], self.login_tokens["refresh"])

    def test_rejects_mismatched_access_token(self):
        other_user = CustomUser.objects.create_user(
            username="other",
            password="secret",
            email="other@example.com",
        )
        other_refresh = RefreshToken.for_user(other_user)
        url = reverse("api_points")
        response = self.client.post(
            url,
            {
                "uuid": str(self.meta.uuid),
                "refresh": self.login_tokens["refresh"],
                "access": str(other_refresh.access_token),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json().get("detail"),
            "Access token does not match refresh token.",
        )

    def test_invalid_refresh_token_returns_error(self):
        url = reverse("api_points")
        response = self.client.post(
            url,
            {
                "uuid": str(self.meta.uuid),
                "refresh": "invalid",  # not a valid token
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("detail"), "Invalid refresh token.")
