from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("merchants", "0010_add_billing_plan_column_if_missing"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchantmeta",
            name="shopify_billing_plan",
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name="merchantmeta",
            name="shopify_billing_status_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
