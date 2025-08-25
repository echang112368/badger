from django.test import TestCase
from django.urls import reverse
import uuid
from accounts.models import CustomUser
from merchants.models import MerchantMeta
from decimal import Decimal
from ledger.models import LedgerEntry
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

    def test_settings_displays_password(self):
        user = CustomUser.objects.create_user(
            username='tester2', password='secret', email='tester2@example.com'
        )
        self.client.login(username='tester2', password='secret')
        response = self.client.get(reverse('user_settings'))
        self.assertContains(response, user.password)

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

    def test_valid_login_returns_token_uuid_name_points(self):
        LedgerEntry.objects.create(
            creator=self.user,
            amount=Decimal("73"),
            entry_type="points",
        )
        url = reverse("api_login")
        response = self.client.post(url, {
            "username": "tester@example.com",
            "password": "pass123",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("token", data)
        self.assertIn("uuid", data)
        self.assertIn("name", data)
        self.assertEqual(data["name"], "Test User")
        self.assertIn("points", data)
        self.assertEqual(data["points"], 73)

    def test_invalid_login_returns_401(self):
        url = reverse("api_login")
        response = self.client.post(url, {
            "username": "tester@example.com",
            "password": "wrong",
        })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("detail"), "Invalid credentials")

    def test_merchant_login_returns_401(self):
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
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("detail"), "Invalid credentials")

    def test_creator_login_returns_401(self):
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
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("detail"), "Invalid credentials")


class PointsAPITests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="points", password="pass123", email="points@example.com"
        )
        LedgerEntry.objects.create(
            creator=self.user,
            amount=Decimal("50"),
            entry_type="points",
        )
        self.meta = CustomerMeta.objects.get(user=self.user)

    def test_points_endpoint_returns_balance(self):
        url = reverse("api_points", kwargs={"uuid": self.meta.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["uuid"], str(self.meta.uuid))
        self.assertEqual(data["points"], 50)

    def test_points_endpoint_unknown_uuid_returns_404(self):
        url = reverse("api_points", kwargs={"uuid": uuid.uuid4()})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


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
