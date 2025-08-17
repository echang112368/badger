from django.db import migrations


def remove_points_if_exists(apps, schema_editor):
    CustomerMeta = apps.get_model("customer", "CustomerMeta")
    table = CustomerMeta._meta.db_table
    cursor = schema_editor.connection.cursor()
    cursor.execute(f"PRAGMA table_info('{table}')")
    columns = [info[1] for info in cursor.fetchall()]
    if "points" in columns:
        field = CustomerMeta._meta.get_field("points")
        schema_editor.remove_field(CustomerMeta, field)


class Migration(migrations.Migration):
    dependencies = [
        ("customer", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(remove_points_if_exists, migrations.RunPython.noop)
            ],
            state_operations=[
                migrations.RemoveField(
                    model_name="customermeta",
                    name="points",
                )
            ],
        )
    ]
