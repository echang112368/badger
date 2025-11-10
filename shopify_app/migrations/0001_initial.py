"""Initial migration for Shopify app models."""

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Shop",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("shop_domain", models.CharField(max_length=255, unique=True)),
                ("access_token", models.CharField(max_length=255)),
            ],
        ),
    ]
