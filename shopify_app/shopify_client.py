import logging
from typing import Any, Dict, List, Optional

import requests


logger = logging.getLogger(__name__)


class ShopifyInvalidCredentialsError(RuntimeError):
    """Raised when Shopify rejects a request due to invalid credentials."""

    def __init__(self, store_domain: str, response):
        self.store_domain = store_domain
        self.response = response
        status = getattr(response, "status_code", "401")
        detail = getattr(response, "text", "") or ""
        message = (
            f"Shopify rejected the request for {store_domain} with HTTP {status}: "
            f"invalid API key or access token."
        )
        if detail:
            message = f"{message} Details: {detail.strip().splitlines()[0]}"
        super().__init__(message)


class ShopifyClient:
    """Helper for making authenticated requests to a Shopify store."""

    def __init__(self, access_token: str, store_domain: str, *, refresh_handler=None):
        self.access_token = access_token
        self.store_domain = store_domain.rstrip('/')
        self._refresh_handler = refresh_handler

    def request(self, method: str, path: str, **kwargs):
        url = f"https://{self.store_domain}{path}"
        base_headers = dict(kwargs.pop("headers", {}) or {})
        base_headers.setdefault("Content-Type", "application/json")
        request_kwargs = dict(kwargs)

        def send(headers_dict):
            return requests.request(method, url, headers=headers_dict, **request_kwargs)

        initial_headers = dict(base_headers)
        initial_headers["X-Shopify-Access-Token"] = self.access_token
        response = send(initial_headers)

        if response.status_code == 401:
            logger.warning(
                "Shopify returned HTTP 401 for %s %s", self.store_domain, path
            )
            if callable(self._refresh_handler):
                new_token = self._refresh_handler()
                if new_token and new_token != self.access_token:
                    self.access_token = new_token
                    retry_headers = dict(base_headers)
                    retry_headers["X-Shopify-Access-Token"] = self.access_token
                    response = send(retry_headers)

        if _is_invalid_token_response(response):
            logger.error(
                "Shopify request failed for %s due to invalid credentials.",
                self.store_domain,
            )
            raise ShopifyInvalidCredentialsError(self.store_domain, response)

        response.raise_for_status()
        return response

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a GraphQL Admin API query and return the parsed JSON body."""

        response = self.post(
            "/admin/api/2024-07/graphql.json",
            json={"query": query, "variables": variables or {}},
        )
        payload = response.json()

        if not isinstance(payload, dict):
            raise ShopifyGraphQLError(
                "Unexpected response type from Shopify GraphQL API.", payload
            )

        errors = payload.get("errors")
        if errors:
            raise ShopifyGraphQLError("Shopify GraphQL request returned errors.", errors)

        return payload

    def get_all_products(self):
        """Fetch all products in the store catalog."""
        products: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            payload = self.graphql(_PRODUCTS_QUERY, {"cursor": cursor})
            data = payload.get("data", {}) or {}
            products_conn = data.get("products") or {}
            edges = products_conn.get("edges") or []
            page_info = products_conn.get("pageInfo") or {}

            for edge in edges:
                node = edge.get("node") or {}
                products.append(_parse_product_node(node))

            if not page_info.get("hasNextPage"):
                break

            cursor = page_info.get("endCursor")
            if not cursor:
                break

        return products


def _is_invalid_token_response(response) -> bool:
    if response is None:
        return False

    if getattr(response, "status_code", None) != 401:
        return False

    body_text = str(getattr(response, "text", "") or "").strip().lower()
    if "invalid api key" in body_text and "access token" in body_text:
        return True
    if "invalid api key or access token" in body_text:
        return True

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        for key in ("errors", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                lowered = value.lower()
                if "invalid api key" in lowered or "access token" in lowered:
                    return True
            elif isinstance(value, (list, tuple)):
                joined = " ".join(str(item) for item in value)
                if "invalid api key" in joined.lower() or "access token" in joined.lower():
                    return True

    return False


class ShopifyGraphQLError(RuntimeError):
    """Raised when Shopify GraphQL API returns an error response."""

    def __init__(self, message: str, details: Any = None):
        if details:
            message = f"{message} Details: {details}"
        super().__init__(message)


def _parse_shopify_gid(gid: Optional[str]) -> Optional[str]:
    """Return the numeric ID from a Shopify GID if possible."""

    if not gid or not isinstance(gid, str):
        return gid

    if gid.startswith("gid://"):
        parts = gid.rsplit("/", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1]

    return gid


def _parse_product_node(node: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Shopify GraphQL product node to resemble REST output."""

    variants = []
    for edge in (node.get("variants", {}).get("edges") or []):
        variant_node = edge.get("node") or {}
        variants.append(
            {
                "id": _parse_shopify_gid(variant_node.get("id")),
                "title": variant_node.get("title"),
                "price": _parse_money_value(variant_node.get("price")),
            }
        )

    images = []
    for edge in (node.get("images", {}).get("edges") or []):
        image_node = edge.get("node") or {}
        images.append({"src": image_node.get("originalSrc")})

    return {
        "id": _parse_shopify_gid(node.get("id")),
        "title": node.get("title"),
        "status": node.get("status"),
        "handle": node.get("handle"),
        "onlineStoreUrl": node.get("onlineStoreUrl"),
        "variants": variants,
        "images": images,
    }


def _parse_money_value(value):
    """Return a simple money amount from a Shopify money field."""

    if isinstance(value, dict):
        return value.get("amount")
    return value


_PRODUCTS_QUERY = """
query getProducts($cursor: String) {
  products(first: 50, after: $cursor) {
    edges {
      cursor
      node {
        id
        title
        status
        handle
        onlineStoreUrl
        variants(first: 50) {
          edges {
            node {
              id
              title
              price {
                amount
                currencyCode
              }
            }
          }
        }
        images(first: 5) {
          edges {
            node {
              originalSrc
            }
          }
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""
