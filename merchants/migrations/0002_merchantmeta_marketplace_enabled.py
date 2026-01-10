from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("merchants", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="merchantmeta",
            name="marketplace_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
