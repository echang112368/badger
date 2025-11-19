from django.db import migrations, models


def add_badger_creator_flag(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    table = CustomUser._meta.db_table
    connection = schema_editor.connection

    with connection.cursor() as cursor:
        column_names = [
            column.name for column in connection.introspection.get_table_description(cursor, table)
        ]

    if "is_default_badger_creator" in column_names:
        return

    field = models.BooleanField(
        default=False,
        help_text=(
            "Marks this creator as the automatic Badger creator that is applied "
            "to merchant accounts. Only one user can be the default at a time."
        ),
    )
    field.set_attributes_from_name("is_default_badger_creator")
    schema_editor.add_field(CustomUser, field)


def noop(apps, schema_editor):
    """No-op reverse migration."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_badger_creator_flag, noop),
    ]
