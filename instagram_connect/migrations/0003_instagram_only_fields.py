from django.db import migrations, models


def backfill_instagram_access_token(apps, schema_editor):
    InstagramConnection = apps.get_model("instagram_connect", "InstagramConnection")
    for connection in InstagramConnection.objects.all().iterator():
        updates = []
        if not connection.instagram_access_token and connection.access_token:
            connection.instagram_access_token = connection.access_token
            updates.append("instagram_access_token")
        if not connection.platform:
            connection.platform = "instagram"
            updates.append("platform")
        if updates:
            connection.save(update_fields=updates)


class Migration(migrations.Migration):

    dependencies = [
        ("instagram_connect", "0002_instagramconnection_meta_tokens"),
    ]

    operations = [
        migrations.AddField(
            model_name="instagramconnection",
            name="instagram_access_token",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="instagramconnection",
            name="platform",
            field=models.CharField(default="instagram", max_length=32),
        ),
        migrations.AddField(
            model_name="instagramconnection",
            name="raw_profile_data",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(backfill_instagram_access_token, migrations.RunPython.noop),
    ]
