from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "merchants",
            "0004_merchantmeta_business_type_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="merchantmeta",
            name="shopify_oauth_authorization_line",
            field=models.CharField(blank=True, max_length=512),
        ),
    ]
