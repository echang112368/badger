from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("shopify_app", "0002_shopifychargerecord"),
    ]

    operations = [
        migrations.DeleteModel(name="ShopifyChargeRecord"),
        migrations.DeleteModel(name="Shop"),
    ]
