from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0002_creatormeta_content_languages_creatormeta_country"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatormeta",
            name="marketplace_enabled",
            field=models.BooleanField(default=False),
        ),
    ]
