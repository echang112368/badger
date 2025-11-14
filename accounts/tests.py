from django.test import TestCase

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
