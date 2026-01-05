from django.db import migrations


def clean_primary_niches(apps, schema_editor):
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    for meta in CreatorMeta.objects.all():
        value = meta.primary_niches

        if value in ("", "None", []):
            meta.primary_niches = None
        elif isinstance(value, str):
            meta.primary_niches = [value]
        else:
            continue

        meta.save(update_fields=["primary_niches"])


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(clean_primary_niches, migrations.RunPython.noop),
    ]
