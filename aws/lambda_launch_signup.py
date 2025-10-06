"""AWS Lambda handler for processing launch page email signups."""

from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import os
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

S3_BUCKET = os.environ.get("SIGNUP_BUCKET", "")
S3_KEY = os.environ.get("SIGNUP_KEY", "launch/emails.csv")

s3_client = boto3.client("s3")


def _build_response(status_code: int, body: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Create a JSON HTTP response with permissive CORS headers."""

    payload = json.dumps(body or {"message": "ok"})
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
        },
        "body": payload,
    }


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body or "").decode("utf-8")
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive logging
        raise ValueError("Invalid JSON payload") from exc


def _load_existing_rows() -> list[dict[str, str]]:
    try:
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
        content = response["Body"].read().decode("utf-8")
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"NoSuchKey", "404"}:
            return []
        raise

    if not content.strip():
        return []

    reader = csv.DictReader(io.StringIO(content))
    return [row for row in reader if row.get("email")]


def _write_rows(rows: list[dict[str, str]]) -> None:
    output = io.StringIO()
    fieldnames = ["email", "timestamp"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({"email": row.get("email", ""), "timestamp": row.get("timestamp", "")})

    s3_client.put_object(Bucket=S3_BUCKET, Key=S3_KEY, Body=output.getvalue().encode("utf-8"))


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda entry point."""

    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method")
        or ""
    ).upper()

    if method == "OPTIONS":
        return _build_response(200, {"message": "ok"})

    if method != "POST":
        return _build_response(405, {"message": "Method not allowed"})

    if not S3_BUCKET:
        return _build_response(500, {"message": "SIGNUP_BUCKET environment variable is not set"})

    try:
        payload = _parse_body(event)
    except ValueError as exc:
        return _build_response(400, {"message": str(exc)})

    email = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return _build_response(400, {"message": "A valid email address is required"})

    timestamp = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    try:
        rows = _load_existing_rows()
    except ClientError as exc:
        return _build_response(500, {"message": "Unable to read signup list", "details": str(exc)})

    if any(row.get("email") == email for row in rows):
        return _build_response(200, {"message": "Email already subscribed"})

    rows.append({"email": email, "timestamp": timestamp})

    try:
        _write_rows(rows)
    except ClientError as exc:
        return _build_response(500, {"message": "Unable to update signup list", "details": str(exc)})

    return _build_response(200, {"message": "Subscription saved"})
