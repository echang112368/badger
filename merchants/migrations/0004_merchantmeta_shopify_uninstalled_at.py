from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("merchants", "0003_itemgroup_return_policy_days"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchantmeta",
            name="shopify_uninstalled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
