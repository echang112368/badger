from django.db import migrations


BADGER_CREATOR_UUID = "733d0d67-6a30-4c48-a92e-b8e211b490f5"
BADGER_CREATOR_USERNAME = "badger"
BADGER_CREATOR_EMAIL = "badger@usebadger.com"


def create_badger_creator(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    badger_meta = CreatorMeta.objects.filter(uuid=BADGER_CREATOR_UUID).first()
    if badger_meta:
        badger_user = badger_meta.user
    else:
        badger_user = CustomUser.objects.filter(
            username=BADGER_CREATOR_USERNAME
        ).first()
        if not badger_user:
            badger_user = CustomUser.objects.filter(
                email=BADGER_CREATOR_EMAIL
            ).first()
        if not badger_user:
            badger_user = CustomUser.objects.create_user(
                username=BADGER_CREATOR_USERNAME,
                email=BADGER_CREATOR_EMAIL,
                password=None,
                first_name="Badger",
                last_name="",
                is_creator=True,
                is_default_badger_creator=True,
            )
            badger_user.set_unusable_password()
            badger_user.save(update_fields=["password"])

    updates = {}
    if not badger_user.is_creator:
        updates["is_creator"] = True
    if not badger_user.is_default_badger_creator:
        updates["is_default_badger_creator"] = True
    if updates:
        for key, value in updates.items():
            setattr(badger_user, key, value)
        badger_user.save(update_fields=list(updates.keys()))

    CreatorMeta.objects.update_or_create(
        user=badger_user, defaults={"uuid": BADGER_CREATOR_UUID}
    )


def remove_badger_creator(apps, schema_editor):
    CustomUser = apps.get_model("accounts", "CustomUser")
    CreatorMeta = apps.get_model("creators", "CreatorMeta")

    CreatorMeta.objects.filter(uuid=BADGER_CREATOR_UUID).delete()
    CustomUser.objects.filter(
        username=BADGER_CREATOR_USERNAME,
        email=BADGER_CREATOR_EMAIL,
        is_default_badger_creator=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("creators", "0003_creatormeta_marketplace_enabled"),
    ]

    operations = [
        migrations.RunPython(create_badger_creator, remove_badger_creator),
    ]
