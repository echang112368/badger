from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('creators', '0006_partnermessage'),
    ]

    operations = [
        migrations.AddField(
            model_name='partnermessage',
            name='is_opening_message',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='partnermessage',
            name='read_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
