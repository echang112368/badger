"""Utility helpers for the merchant whitelist app."""
from __future__ import annotations

from typing import Iterable, List, Set
from urllib.parse import urlparse

from django.apps import apps

from .models import Merchant


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
