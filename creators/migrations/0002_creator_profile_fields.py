from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("creators", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatormeta",
            name="short_pitch",
            field=models.CharField(blank=True, max_length=240),
        ),
        migrations.AddField(
            model_name="creatormeta",
            name="social_media_profiles",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="creatormeta",
            name="content_skills",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
