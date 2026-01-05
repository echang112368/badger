from django.db import migrations


def create_creator_meta(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    for user in CustomUser.objects.filter(is_creator=True):
        CreatorMeta.objects.get_or_create(
            user=user,
            defaults={"display_name": user.username},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_creator_meta, migrations.RunPython.noop),
    ]
