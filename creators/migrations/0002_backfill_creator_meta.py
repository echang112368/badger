from django.db import migrations


CREATOR_META_FIELDS = [
    "bio",
    "paypal_email",
    "uuid",
    "display_name",
    "country",
    "primary_niches",
    "platforms",
    "content_style_tags",
    "posting_frequency",
    "open_to_gifting",
    "payout_method",
    "tax_info_submitted",
    "onboarding_step",
    "onboarding_completed",
    "onboarding_completion_percent",
    "onboarding_content_skipped",
    "onboarding_performance_skipped",
]


def ensure_creator_meta_columns(apps, schema_editor):
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, CreatorMeta._meta.db_table
            )
        }

    for field_name in CREATOR_META_FIELDS:
        if field_name in existing_columns:
            continue
        schema_editor.add_field(
            CreatorMeta,
            CreatorMeta._meta.get_field(field_name),
        )


def create_creator_meta(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    for user in CustomUser.objects.filter(is_creator=True):
        CreatorMeta.objects.get_or_create(
            user=user,
            defaults={"display_name": user.username},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_creator_meta_columns, migrations.RunPython.noop),
        migrations.RunPython(create_creator_meta, migrations.RunPython.noop),
    ]
