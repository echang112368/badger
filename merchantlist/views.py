import hashlib
import json
from json import JSONDecodeError
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.http import HttpResponseNotModified, JsonResponse
from django.utils import timezone
from django.utils.http import http_date, parse_http_date_safe
from django.views.decorators.csrf import csrf_exempt

from .models import Config
from .utils import collect_merchant_domains


def _get_active_config() -> Config:
    """Return the most recently updated configuration, creating one if needed."""

    config = Config.objects.order_by("-updated_at", "-pk").first()
    if config is None:
        config = Config.objects.create()
    return config

BASE_DIR = Path(__file__).resolve().parent
STATIC_FILE = BASE_DIR / "static" / "merchant_list.json"


def _check_cache_headers(request, etag: str, last_modified: datetime):
    if_none_match = request.headers.get("If-None-Match")
    if if_none_match and if_none_match.strip() == etag:
        return HttpResponseNotModified()

    if_modified_since = request.headers.get("If-Modified-Since")
    if if_modified_since:
        modified_since = parse_http_date_safe(if_modified_since)
        if modified_since is not None:
            timestamp = int(last_modified.timestamp())
            if timestamp <= modified_since:
                return HttpResponseNotModified()

    return None


@csrf_exempt
def merchant_meta(request):
    config = _get_active_config()
    updated_at = config.updated_at or timezone.now()
    updated_utc = timezone.localtime(updated_at, dt_timezone.utc)
    etag = f'W/"merchant-meta-{config.merchant_version}-{int(updated_utc.timestamp())}"'

    cache_response = _check_cache_headers(request, etag, updated_utc)
    if isinstance(cache_response, HttpResponseNotModified):
        return cache_response

    payload = {
        "version": config.merchant_version,
        "updated": updated_utc.isoformat().replace("+00:00", "Z"),
    }
    response = JsonResponse(payload)
    response["ETag"] = etag
    response["Last-Modified"] = http_date(updated_utc.timestamp())
    return response


@csrf_exempt
def merchant_list(request):
    config = _get_active_config()
    updated_at = config.updated_at or timezone.now()
    updated_utc = timezone.localtime(updated_at, dt_timezone.utc)
    updated_iso = updated_utc.isoformat().replace("+00:00", "Z")

    merchants: list[str] = []
    file_updated = None
    file_data: dict | None = None
    if STATIC_FILE.exists():
        try:
            with STATIC_FILE.open("r", encoding="utf-8") as fp:
                file_data = json.load(fp)
        except (OSError, JSONDecodeError):
            file_data = None

        if file_data:
            merchants = file_data.get("merchants", [])
            raw_updated = file_data.get("updated")
            if raw_updated:
                try:
                    file_updated = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                except ValueError:
                    file_updated = None

    needs_refresh = True
    if file_data:
        needs_refresh = not (
            file_data.get("version") == config.merchant_version
            and file_data.get("updated") == updated_iso
            and isinstance(merchants, list)
        )

    if needs_refresh:
        merchants = collect_merchant_domains()
        file_updated = updated_utc
        payload = {
            "version": config.merchant_version,
            "updated": updated_iso,
            "merchants": merchants,
        }
        try:
            STATIC_FILE.parent.mkdir(parents=True, exist_ok=True)
            with STATIC_FILE.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
        except OSError:
            pass
    else:
        payload = {
            "version": config.merchant_version,
            "updated": updated_iso,
            "merchants": merchants,
        }

    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    etag = f'W/"merchant-list-{digest}"'

    last_modified = file_updated or updated_utc

    cache_response = _check_cache_headers(request, etag, last_modified)
    if isinstance(cache_response, HttpResponseNotModified):
        return cache_response

    response = JsonResponse(payload)
    response["ETag"] = etag
    response["Last-Modified"] = http_date(last_modified.timestamp())
    return response
