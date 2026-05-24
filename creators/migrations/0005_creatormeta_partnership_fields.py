from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0004_socialanalyticssnapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="creatormeta",
            name="affiliate_brand_deals_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="creatormeta",
            name="avg_sponsored_conversion_rate_pct",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="creatormeta",
            name="gifted_brand_deals_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="creatormeta",
            name="paid_brand_deals_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="creatormeta",
            name="partnership_history_notes",
            field=models.TextField(blank=True),
        ),
    ]
