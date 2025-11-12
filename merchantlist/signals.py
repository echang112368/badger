"""Signals for keeping the merchant whitelist in sync."""
from __future__ import annotations

from typing import Any, Optional

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Merchant
from .utils import _normalize_domain, publish_merchant_config


@receiver(post_save, sender=Merchant)
def publish_on_new_curated_merchant(
    sender: type[Merchant], instance: Merchant, created: bool, **_: Any
) -> None:
    """Automatically publish when a curated merchant is added."""
    if not created:
        return

    transaction.on_commit(lambda: publish_merchant_config())


@receiver(post_delete, sender=Merchant)
def publish_on_removed_merchant(
    sender: type[Merchant], instance: Merchant, **_: Any
) -> None:
    """Ensure the merchant list refreshes when entries are removed."""

    transaction.on_commit(lambda: publish_merchant_config())


def _extract_domain(value_source: Any) -> str:
    """Return the best domain string from a merchant meta instance."""
    if hasattr(value_source, "domain"):
        return getattr(value_source, "domain", "")
    if hasattr(value_source, "shopify_store_domain"):
        return getattr(value_source, "shopify_store_domain", "")
    return ""


def _derive_account_name(meta: Any, domain: str) -> str:
    if hasattr(meta, "user") and meta.user is not None:  # type: ignore[attr-defined]
        user = meta.user  # type: ignore[attr-defined]
        full_name_fn = getattr(user, "get_full_name", None)
        if callable(full_name_fn):
            full_name = (full_name_fn() or "").strip()
        else:
            full_name = ""

        username_fn = getattr(user, "get_username", None)
        if callable(username_fn):
            username = username_fn()
        else:
            username = getattr(user, "username", "")

        return full_name or username
    if hasattr(meta, "company_name"):
        company = getattr(meta, "company_name", "")
        if company:
            return company
    return domain


def _sync_auto_managed_merchant(meta: Any, previous_domain: Optional[str] = None) -> None:
    domain = _normalize_domain(_extract_domain(meta))
    account = getattr(meta, "user", None)
    business_type = getattr(meta, "business_type", Merchant.MerchantType.INDEPENDENT)

    if previous_domain and previous_domain != domain:
        Merchant.objects.filter(
            domain=previous_domain,
            auto_managed=True,
        ).delete()

    if not domain:
        if account:
            Merchant.objects.filter(account=account, auto_managed=True).delete()
        return

    defaults = {
        "account": account,
        "account_name": _derive_account_name(meta, domain),
        "business_type": business_type,
        "auto_managed": True,
    }
    Merchant.objects.update_or_create(domain=domain, defaults=defaults)


def _merchant_meta_pre_save(sender: type[Any], instance: Any, **_: Any) -> None:
    """Remember whether the merchant meta domain changed in this save."""
    previous_normalized = ""
    if instance.pk:
        try:
            previous = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:  # pragma: no cover - defensive
            previous_normalized = ""
        else:
            previous_normalized = _normalize_domain(_extract_domain(previous))

    current_normalized = _normalize_domain(_extract_domain(instance))
    instance._merchant_domain_changed = previous_normalized != current_normalized  # type: ignore[attr-defined]
    instance._merchant_previous_domain = previous_normalized  # type: ignore[attr-defined]


def _merchant_meta_post_save(sender: type[Any], instance: Any, **_: Any) -> None:
    """Publish a new merchant list if the merchant domain changed."""
    previous_domain = getattr(instance, "_merchant_previous_domain", "")
    if hasattr(instance, "_merchant_previous_domain"):
        delattr(instance, "_merchant_previous_domain")

    _sync_auto_managed_merchant(instance, previous_domain=previous_domain)

    domain_changed = getattr(instance, "_merchant_domain_changed", False)
    if hasattr(instance, "_merchant_domain_changed"):
        delattr(instance, "_merchant_domain_changed")

    if not domain_changed:
        return

    transaction.on_commit(lambda: publish_merchant_config())


def _merchant_meta_post_delete(sender: type[Any], instance: Any, **_: Any) -> None:
    """Remove managed merchant entries when the account is deleted."""

    domain = _normalize_domain(_extract_domain(instance))
    account = getattr(instance, "user", None)

    filters = {"auto_managed": True}
    if domain:
        Merchant.objects.filter(domain=domain, **filters).delete()
    if account:
        Merchant.objects.filter(account=account, **filters).delete()

    transaction.on_commit(lambda: publish_merchant_config())


def register_signals() -> None:
    """Hook up dynamic signal handlers for optional merchant models."""
    MerchantMeta = apps.get_model("merchants", "MerchantMeta")
    if MerchantMeta is None:
        return

    pre_save.connect(
        _merchant_meta_pre_save,
        sender=MerchantMeta,
        dispatch_uid="merchantlist.merchantmeta.pre_save",
    )
    post_save.connect(
        _merchant_meta_post_save,
        sender=MerchantMeta,
        dispatch_uid="merchantlist.merchantmeta.post_save",
    )
    post_delete.connect(
        _merchant_meta_post_delete,
        sender=MerchantMeta,
        dispatch_uid="merchantlist.merchantmeta.post_delete",
    )
