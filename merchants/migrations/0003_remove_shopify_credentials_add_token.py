from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("merchants", "0002_merchantmeta_shopify_store_domain"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="merchantmeta",
            name="shopify_api_key",
        ),
        migrations.RemoveField(
            model_name="merchantmeta",
            name="shopify_api_password",
        ),
        migrations.AddField(
            model_name="merchantmeta",
            name="shopify_access_token",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
