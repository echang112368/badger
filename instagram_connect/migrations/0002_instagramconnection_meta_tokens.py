from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("instagram_connect", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="instagramconnection",
            name="page_access_token",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="instagramconnection",
            name="user_access_token",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
