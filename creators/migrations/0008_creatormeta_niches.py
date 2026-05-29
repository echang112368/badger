from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0007_partnermessage_new_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatormeta",
            name="niches",
            field=models.JSONField(default=list, blank=True),
        ),
    ]
