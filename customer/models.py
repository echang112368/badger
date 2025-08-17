from django.db import models
import uuid
from django.contrib.auth import get_user_model


User = get_user_model()


class CustomerMeta(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    def __str__(self):
        return self.user.username
