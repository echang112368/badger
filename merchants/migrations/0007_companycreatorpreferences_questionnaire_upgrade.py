from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("merchants", "0006_companycreatorpreferences_budget_min_max"),
    ]

    operations = [
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="brand_tone_keywords",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="has_run_influencer_campaigns_before",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="minimum_engagement_rate",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="past_campaign_learnings",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="preferred_platforms",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="success_metric_priority",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="target_customer_age_range",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="target_customer_gender_skew",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="companycreatorpreferences",
            name="target_customer_location",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AlterField(
            model_name="companycreatorpreferences",
            name="campaign_stage",
            field=models.CharField(
                blank=True,
                choices=[
                    ("exploring", "Exploring (broader discovery)"),
                    ("ready_to_contact", "Ready to contact (shortlist quality)"),
                    ("active_campaign", "Active campaign (execution-focused)"),
                ],
                max_length=32,
            ),
        ),
    ]
