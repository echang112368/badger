from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """Custom user model with unique email field."""

    email = models.EmailField(unique=True)
    is_merchant = models.BooleanField(default=False)
    is_creator = models.BooleanField(default=False)
