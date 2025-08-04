from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("merchants", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE merchants_merchantmeta DROP COLUMN IF EXISTS contact_email;",
            reverse_sql="ALTER TABLE merchants_merchantmeta ADD COLUMN contact_email varchar(254);",
        ),
    ]
