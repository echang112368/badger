from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchantinvoice",
            name="provider",
            field=models.CharField(
                choices=[("paypal", "PayPal"), ("shopify", "Shopify")],
                default="paypal",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="merchantinvoice",
            name="shopify_charge_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="merchantinvoice",
            name="shopify_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="merchantinvoice",
            name="shopify_status",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
