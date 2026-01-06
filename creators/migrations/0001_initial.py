from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0003_customuser_email_verified_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="CreatorMeta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bio", models.TextField(blank=True)),
                ("paypal_email", models.EmailField(blank=True, max_length=254)),
                ("uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("display_name", models.CharField(blank=True, max_length=120)),
                ("country", models.CharField(blank=True, max_length=100)),
                ("primary_niches", models.JSONField(blank=True, default=list)),
                ("platforms", models.JSONField(blank=True, default=list)),
                ("content_style_tags", models.JSONField(blank=True, default=list)),
                ("posting_frequency", models.CharField(blank=True, max_length=120)),
                ("open_to_gifting", models.BooleanField(blank=True, null=True)),
                ("payout_method", models.CharField(blank=True, max_length=120)),
                ("tax_info_submitted", models.BooleanField(default=False)),
                (
                    "onboarding_step",
                    models.CharField(
                        choices=[
                            ("identity", "Identity"),
                            ("platforms", "Platforms"),
                            ("content", "Content"),
                            ("performance", "Performance"),
                            ("payouts", "Payouts"),
                            ("complete", "Complete"),
                        ],
                        default="identity",
                        max_length=32,
                    ),
                ),
                ("onboarding_completed", models.BooleanField(default=False)),
                ("onboarding_completion_percent", models.IntegerField(default=0)),
                ("onboarding_content_skipped", models.BooleanField(default=False)),
                ("onboarding_performance_skipped", models.BooleanField(default=False)),
                (
                    "user",
                    models.OneToOneField(on_delete=models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
                ),
            ],
        ),
    ]
