from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="CreatorMeta",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("bio", models.TextField(blank=True)),
                        ("paypal_email", models.EmailField(blank=True, max_length=254)),
                        ("social_media_platform", models.CharField(blank=True, max_length=100)),
                        ("follower_range", models.CharField(blank=True, max_length=50)),
                        ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                        ("user", models.OneToOneField(on_delete=models.CASCADE, to=settings.AUTH_USER_MODEL)),
                    ],
                ),
            ],
        ),
    ]
