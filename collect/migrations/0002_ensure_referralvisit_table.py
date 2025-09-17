from django.db import migrations


def ensure_referralvisit_table(apps, schema_editor):
    """Create the ReferralVisit table if it is missing.

    Early deployments of the ``collect`` app ran the initial migration
    before the ``ReferralVisit`` model was added. Those databases therefore
    recorded ``0001_initial`` as applied but never created the table.  When
    the application code later started querying ``ReferralVisit`` it
    resulted in ``OperationalError: no such table``.  This migration
    backfills the missing schema by creating the table when it is absent.
    """

    ReferralVisit = apps.get_model("collect", "ReferralVisit")
    table_name = ReferralVisit._meta.db_table

    existing_tables = schema_editor.connection.introspection.table_names()
    if table_name in existing_tables:
        # The table already exists (for example on fresh installs where
        # ``0001_initial`` created it), so there's nothing to do.
        return

    schema_editor.create_model(ReferralVisit)


class Migration(migrations.Migration):

    dependencies = [
        ("collect", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_referralvisit_table, migrations.RunPython.noop),
    ]
