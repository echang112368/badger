from django.contrib.auth.models import AbstractUser
from django.db import models

class CustomUser(AbstractUser):
    is_merchant = models.BooleanField(default = False)
    is_creator = models.BooleanField(default = False)
