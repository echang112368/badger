from django.test import TestCase
from django.contrib.auth import get_user_model
import uuid


class CustomUserModelTests(TestCase):
    def test_uuid_is_created_and_unique(self):
        User = get_user_model()
        user1 = User.objects.create_user(username="user1", password="testpass123")
        user2 = User.objects.create_user(username="user2", password="testpass123")

        self.assertIsNotNone(user1.uuid)
        self.assertIsInstance(user1.uuid, uuid.UUID)
        self.assertIsNotNone(user2.uuid)
        self.assertNotEqual(user1.uuid, user2.uuid)
