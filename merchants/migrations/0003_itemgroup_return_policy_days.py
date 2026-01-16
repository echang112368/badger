from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("merchants", "0002_merchantmeta_marketplace_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemgroup",
            name="return_policy_days",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
