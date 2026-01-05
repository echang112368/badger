import json

from django.db import migrations


def _normalize_primary_niches(value):
    if value in (None, "", "None", "[]"):
        return None

    if isinstance(value, (list, tuple)):
        return list(value)

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return [value]
        if parsed in ("", "None", []):
            return None
        if isinstance(parsed, str):
            return [parsed]
        if isinstance(parsed, list):
            return parsed
        return None

    return None


def clean_primary_niches(apps, schema_editor):
    CreatorMeta = apps.get_model("creators", "CreatorMeta")
    table_name = CreatorMeta._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT id, primary_niches FROM {table_name}")
        rows = cursor.fetchall()

    updates = []
    for row_id, value in rows:
        normalized = _normalize_primary_niches(value)
        if normalized == value:
            continue
        updates.append((json.dumps(normalized) if normalized is not None else None, row_id))

    if not updates:
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.executemany(
            f"UPDATE {table_name} SET primary_niches = ? WHERE id = ?",
            updates,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0002_add_primary_niches"),
    ]

    operations = [
        migrations.RunPython(clean_primary_niches, migrations.RunPython.noop),
    ]
