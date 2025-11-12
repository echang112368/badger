"""Signals for keeping the merchant whitelist in sync."""
from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_save, pre_save
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


def _extract_domain(value_source: Any) -> str:
    """Return the best domain string from a merchant meta instance."""
    if hasattr(value_source, "domain"):
        return getattr(value_source, "domain", "")
    if hasattr(value_source, "shopify_store_domain"):
        return getattr(value_source, "shopify_store_domain", "")
    return ""


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


def _merchant_meta_post_save(sender: type[Any], instance: Any, **_: Any) -> None:
    """Publish a new merchant list if the merchant domain changed."""
    domain_changed = getattr(instance, "_merchant_domain_changed", False)
    if hasattr(instance, "_merchant_domain_changed"):
        delattr(instance, "_merchant_domain_changed")

    if not domain_changed:
        return

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
