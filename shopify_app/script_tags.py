"""Utilities for managing Shopify script tags."""

from __future__ import annotations

import logging

from merchants.models import MerchantMeta

from .shopify_client import ShopifyClient, ShopifyGraphQLError
from .token_management import refresh_shopify_token

logger = logging.getLogger(__name__)

SCRIPT_SRCS = [
    "https://6457c6b55211.ngrok-free.app/static/js/referral_tracker.js",
]
LEGACY_SCRIPT_SRCS = [
    "https://6457c6b55211.ngrok-free.app/static/js/cart_attributes.js",
]


SCRIPT_TAGS_QUERY = """
query ScriptTags {
  scriptTags(first: 100) {
    edges {
      node {
        id
        src
        displayScope
      }
    }
  }
}
"""


SCRIPT_TAG_CREATE_MUTATION = """
mutation CreateScriptTag($src: URL!) {
  scriptTagCreate(input: {src: $src, displayScope: ONLINE_STORE}) {
    scriptTag {
      id
      src
      displayScope
    }
    userErrors {
      field
      message
    }
  }
}
"""

SCRIPT_TAG_DELETE_MUTATION = """
mutation DeleteScriptTag($id: ID!) {
  scriptTagDelete(id: $id) {
    deletedScriptTagId
    userErrors {
      field
      message
    }
  }
}
"""


def ensure_script_tags(
    client: ShopifyClient,
    *,
    script_srcs: list[str] | None = None,
    legacy_script_srcs: list[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Ensure that desired scripts are present and legacy scripts are removed."""

    scripts_to_add = script_srcs or SCRIPT_SRCS
    legacy_scripts = legacy_script_srcs or LEGACY_SCRIPT_SRCS

    tags = _fetch_script_tags(client)
    _remove_legacy_scripts(client, tags, legacy_scripts)

    existing_srcs = {tag.get("src") for tag in tags if tag.get("src")}
    injected_srcs: set[str] = set()
    for src in scripts_to_add:
        if src in existing_srcs:
            continue
        _create_script_tag(client, src)
        injected_srcs.add(src)

    return injected_srcs, existing_srcs


def inject_scripts_for_merchant(merchant: MerchantMeta) -> tuple[set[str], set[str]]:
    """Inject script tags for the provided merchant."""

    access_token = merchant.shopify_access_token
    store_domain = getattr(merchant, "shopify_store_domain", None)
    if not (access_token and store_domain):
        raise ValueError("Missing Shopify credentials for script injection.")

    client = ShopifyClient(
        access_token,
        store_domain,
        refresh_handler=lambda m=merchant: refresh_shopify_token(m),
        token_type="offline",
    )
    return ensure_script_tags(client)


def _fetch_script_tags(client: ShopifyClient) -> list[dict]:
    payload = client.graphql(SCRIPT_TAGS_QUERY)
    edges = (payload.get("data", {}).get("scriptTags", {}).get("edges") or [])
    return [edge.get("node") or {} for edge in edges]


def _create_script_tag(client: ShopifyClient, src: str) -> None:
    payload = client.graphql(SCRIPT_TAG_CREATE_MUTATION, {"src": src})
    result = payload.get("data", {}).get("scriptTagCreate") or {}
    errors = result.get("userErrors") or []
    if errors:
        raise ShopifyGraphQLError("Failed to create script tag.", errors)


def _remove_legacy_scripts(
    client: ShopifyClient, tags: list[dict], legacy_script_srcs: list[str]
) -> None:
    legacy_tags = [
        tag for tag in tags if tag.get("src") in legacy_script_srcs and tag.get("id")
    ]
    for tag in legacy_tags:
        payload = client.graphql(SCRIPT_TAG_DELETE_MUTATION, {"id": tag["id"]})
        result = payload.get("data", {}).get("scriptTagDelete") or {}
        errors = result.get("userErrors") or []
        if errors:
            raise ShopifyGraphQLError("Failed to delete script tag.", errors)
