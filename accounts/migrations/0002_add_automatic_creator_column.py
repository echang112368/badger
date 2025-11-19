from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="automatic_creator",
            field=models.BooleanField(
                default=False,
                help_text="Designates the built-in Badger creator that is auto-linked to merchants.",
            ),
        ),
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS unique_automatic_creator;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="customuser",
            constraint=models.UniqueConstraint(
                fields=["automatic_creator"],
                condition=Q(automatic_creator=True),
                name="unique_automatic_creator",
            ),
        ),
    ]
