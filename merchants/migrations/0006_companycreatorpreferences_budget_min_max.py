from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("merchants", "0005_companycreatorpreferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="budget_max",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="budget_min",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
