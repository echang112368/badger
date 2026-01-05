import json

from django.db import migrations


JSON_LIST_FIELDS = ("platforms", "primary_niches")
JSON_DICT_FIELDS = ("audience_breakdown",)


def _safe_parse_json(value):
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _normalize_list(value):
    if value is None:
        return None
    parsed = _safe_parse_json(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, str):
        return [parsed]
    return []


def _normalize_dict(value):
    if value is None:
        return None
    parsed = _safe_parse_json(value)
    if isinstance(parsed, dict):
        return parsed
    return {}


def sanitize_json_fields(apps, schema_editor):
    # SQLite enforces JSON validity via CHECK(JSON_VALID(...)) when tables are
    # rebuilt. This migration normalizes legacy values before any schema
    # changes touch JSONField columns.
    CreatorMeta = apps.get_model("creators", "CreatorMeta")
    table_name = CreatorMeta._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }

    target_fields = [
        field_name
        for field_name in (*JSON_LIST_FIELDS, *JSON_DICT_FIELDS)
        if field_name in existing_columns
    ]
    if not target_fields:
        return

    select_columns = ", ".join(["id", *target_fields])
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT {select_columns} FROM {table_name}")
        rows = cursor.fetchall()

    updates = []
    for row in rows:
        row_id = row[0]
        values = dict(zip(target_fields, row[1:]))
        changed = {}

        for field_name in JSON_LIST_FIELDS:
            if field_name not in values:
                continue
            normalized = _normalize_list(values[field_name])
            if normalized != values[field_name]:
                changed[field_name] = normalized

        for field_name in JSON_DICT_FIELDS:
            if field_name not in values:
                continue
            normalized = _normalize_dict(values[field_name])
            if normalized != values[field_name]:
                changed[field_name] = normalized

        if not changed:
            continue

        assignments = []
        params = []
        for field_name, normalized in changed.items():
            assignments.append(f"{field_name} = ?")
            params.append(
                json.dumps(normalized) if normalized is not None else None
            )
        params.append(row_id)

        updates.append((f"UPDATE {table_name} SET {', '.join(assignments)} WHERE id = ?", params))

    if not updates:
        return

    with schema_editor.connection.cursor() as cursor:
        for statement, params in updates:
            cursor.execute(statement, params)


class Migration(migrations.Migration):

    dependencies = [
        ("creators", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(sanitize_json_fields, migrations.RunPython.noop),
    ]
