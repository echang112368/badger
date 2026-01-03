from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("merchants", "0011_add_shopify_billing_verification_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchantitem",
            name="image_url",
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="merchantitem",
            name="price",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
    ]
