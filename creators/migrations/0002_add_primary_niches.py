from django.db import migrations


def add_primary_niches_column(apps, schema_editor):
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, CreatorMeta._meta.db_table
            )
        }

    if "primary_niches" in existing_columns:
        return

    schema_editor.add_field(
        CreatorMeta,
        CreatorMeta._meta.get_field("primary_niches"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_primary_niches_column, migrations.RunPython.noop),
    ]
