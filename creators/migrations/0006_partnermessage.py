from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0005_creatormeta_partnership_fields"),
        ("links", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PartnerMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "partnership",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="links.merchantcreatorlink"),
                ),
                (
                    "sender",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
