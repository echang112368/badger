"""Helpers for managing Shopify access token refresh."""

from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from merchants.models import MerchantMeta

from .oauth import ShopifyOAuthError, normalise_shop_domain, refresh_access_token


logger = logging.getLogger(__name__)


def refresh_shopify_token(meta: MerchantMeta) -> Optional[str]:
    """Refresh the stored Shopify access token for the merchant.

    Returns the new access token when refresh succeeds. If refresh fails, the
    stored access token is cleared to force a new OAuth installation.
    """

    if not meta or not meta.shopify_store_domain:
        return None

    refresh_token = getattr(meta, "shopify_refresh_token", "") or ""
    if not refresh_token:
        return None

    shop = normalise_shop_domain(meta.shopify_store_domain)
    if not shop:
        return None

    try:
        response = refresh_access_token(shop, refresh_token)
    except ShopifyOAuthError:
        # Clear the invalid tokens so future requests prompt a reinstallation and
        # we stop retrying with a bad refresh token.
        logger.warning(
            "Failed to refresh Shopify access token for %s. Clearing stored token.",
            shop,
        )

        update_fields = ["shopify_access_token"]
        meta.shopify_access_token = ""
        if hasattr(meta, "shopify_refresh_token") and meta.shopify_refresh_token:
            meta.shopify_refresh_token = ""
            update_fields.append("shopify_refresh_token")

        meta.save(update_fields=update_fields)
        return None

    new_access_token = response.access_token
    new_refresh_token = response.refresh_token or refresh_token

    update_fields = ["shopify_access_token"]
    meta.shopify_access_token = new_access_token
    if hasattr(meta, "shopify_refresh_token"):
        meta.shopify_refresh_token = new_refresh_token
        update_fields.append("shopify_refresh_token")

    with transaction.atomic():
        meta.save(update_fields=update_fields)
        logger.info(
            "Saved refreshed Shopify tokens for %s: access_token=%s refresh_token=%s",
            shop,
            new_access_token,
            new_refresh_token,
        )

    return new_access_token


def clear_shopify_token_for_shop(shop_domain: str) -> Optional[MerchantMeta]:
    """Remove stored Shopify tokens for the given shop and drop duplicates."""

    normalised = normalise_shop_domain(shop_domain)
    if not normalised:
        return None

    with transaction.atomic():
        metas = list(
            MerchantMeta.objects.select_for_update()
            .filter(shopify_store_domain__iexact=normalised)
            .order_by("pk")
        )

        if not metas:
            return None

        primary = metas[0]
        duplicates = metas[1:]
        if duplicates:
            duplicate_ids = [meta.pk for meta in duplicates]
            logger.warning(
                "Deleting duplicate MerchantMeta rows for %s: %s",
                normalised,
                duplicate_ids,
            )
            MerchantMeta.objects.filter(pk__in=duplicate_ids).delete()

        fields = []
        if primary.shopify_access_token:
            primary.shopify_access_token = ""
            fields.append("shopify_access_token")
        if getattr(primary, "shopify_refresh_token", ""):
            primary.shopify_refresh_token = ""
            fields.append("shopify_refresh_token")

        if fields:
            primary.save(update_fields=fields)
            logger.info(
                "Cleared Shopify tokens stored for %s (MerchantMeta %s).",
                normalised,
                primary.pk,
            )

        return primary


__all__ = ["clear_shopify_token_for_shop", "refresh_shopify_token"]
