"""Utility helpers for the merchant whitelist app."""
from __future__ import annotations

import json
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Iterable, List, Set, Tuple
from urllib.parse import urlparse

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from .models import Config, Merchant


def _normalize_domain(value: str | None) -> str:
    """Normalize a domain string for consistent comparisons."""
    if not value:
        return ""

    domain = value.strip()
    if not domain:
        return ""

    # Preserve wildcard prefixes while still stripping protocols.
    has_wildcard = domain.startswith("*.")
    if "://" in domain:
        parsed = urlparse(domain)
        host = parsed.netloc or parsed.path
    else:
        host = domain

    host = host.strip().lower().rstrip("/")
    if has_wildcard and not host.startswith("*."):
        host = f"*.{host.lstrip('*.')}"
    return host


def _iter_account_domains() -> Iterable[str]:
    """Yield domain strings from merchant accounts if available."""
    if not apps.is_installed("merchants"):
        return []

    MerchantMeta = apps.get_model("merchants", "MerchantMeta")
    if MerchantMeta is None:
        return []

    field_name = None
    for field in MerchantMeta._meta.get_fields():  # type: ignore[attr-defined]
        if field.name == "domain":
            field_name = "domain"
            break
        if field.name == "shopify_store_domain":
            field_name = "shopify_store_domain"
    if field_name is None:
        return []

    queryset = MerchantMeta.objects.exclude(**{f"{field_name}__isnull": True}).exclude(
        **{field_name: ""}
    )
    return queryset.values_list(field_name, flat=True)


def collect_merchant_domains() -> List[str]:
    """Collect domains from both curated entries and merchant accounts."""
    domains: Set[str] = set()

    for domain in Merchant.objects.values_list("domain", flat=True):
        normalized = _normalize_domain(domain)
        if normalized:
            domains.add(normalized)

    for domain in _iter_account_domains():
        normalized = _normalize_domain(domain)
        if normalized:
            domains.add(normalized)

    return sorted(domains)


def publish_merchant_config(config: Config | None = None) -> Tuple[Config, dict]:
    """Publish the merchant JSON payload and bump the config version.

    The helper centralises the logic shared between the admin action and the
    management command to ensure the JSON file and configuration stay in sync.
    """

    merchants = collect_merchant_domains()
    static_path = Path(__file__).resolve().parent / "static" / "merchant_list.json"
    static_path.parent.mkdir(parents=True, exist_ok=True)

    with transaction.atomic():
        qs = Config.objects.select_for_update().order_by("-updated_at", "-pk")
        if config is not None:
            config = qs.get(pk=config.pk)
        else:
            config = qs.first()
            if config is None:
                config = Config.objects.create()

        config.merchant_version += 1
        config.save(update_fields=["merchant_version", "updated_at"])

        updated_utc = timezone.localtime(config.updated_at, dt_timezone.utc)
        payload = {
            "version": config.merchant_version,
            "updated": updated_utc.isoformat().replace("+00:00", "Z"),
            "merchants": merchants,
        }

        with static_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            fp.write("\n")

    return config, payload
