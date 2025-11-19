from django.db import migrations, models


def add_billing_plan_column_if_missing(apps, schema_editor):
    MerchantMeta = apps.get_model("merchants", "MerchantMeta")
    connection = schema_editor.connection
    table_name = MerchantMeta._meta.db_table
    column_names = [
        column.name
        for column in connection.introspection.get_table_description(
            connection.cursor(), table_name
        )
    ]

    if "billing_plan" in column_names:
        return

    field = models.CharField(
        max_length=32,
        choices=[
            ("platform_only", "Platform only ($80/mo)"),
            ("badger_creator", "Badger Creator included ($30/mo)"),
        ],
        default="badger_creator",
        help_text=(
            "Choose between the platform-only plan and the plan that includes "
            "the automatic Badger creator."
        ),
    )
    field.set_attributes_from_name("billing_plan")
    schema_editor.add_field(MerchantMeta, field)


class Migration(migrations.Migration):

    dependencies = [
        ("merchants", "0009_align_billing_plans"),
    ]

    operations = [
        migrations.RunPython(add_billing_plan_column_if_missing, migrations.RunPython.noop),
    ]
