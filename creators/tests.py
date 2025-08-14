from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser


class CreatorSettingsTests(TestCase):
    def test_settings_displays_email(self):
        user = CustomUser.objects.create_user(
            username="creator",
            password="pass",
            email="creator@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_settings"))
        self.assertContains(response, user.email)

    def test_settings_displays_password(self):
        user = CustomUser.objects.create_user(
            username="creator2",
            password="pass123",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_settings"))
        self.assertContains(response, user.password)

