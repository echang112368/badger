"""Helpers for managing Shopify access token refresh."""

from __future__ import annotations

from typing import Optional

from django.db import transaction

from merchants.models import MerchantMeta

from .oauth import ShopifyOAuthError, normalise_shop_domain, refresh_access_token


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
        # Clear the invalid token so future requests prompt a reinstallation.
        meta.shopify_access_token = ""
        meta.save(update_fields=["shopify_access_token"])
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

    return new_access_token


__all__ = ["refresh_shopify_token"]
