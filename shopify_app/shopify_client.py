import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

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
            logger.error("Shopify GraphQL returned errors for %s: %s", query, errors)
            raise ShopifyGraphQLError("Shopify GraphQL request returned errors.", errors)

        return payload

    def create_app_subscription(
        self,
        plan_name: str,
        price_amount: Decimal,
        return_url: str,
        *,
        test_mode: bool = True,
    ) -> Dict[str, Any]:
        """Create a Shopify app subscription using the Billing V2 schema."""

        from .billing import ShopifyBillingError  # imported lazily to avoid cycles

        if not plan_name or not return_url:
            raise ShopifyBillingError(
                "A plan name and return URL are required to create a Shopify subscription."
            )

        try:
            normalized_price = Decimal(str(price_amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ShopifyBillingError("Invalid recurring price for Shopify subscription.") from exc

        if normalized_price <= 0:
            raise ShopifyBillingError("Recurring price must be greater than zero.")

        recurring_plan: Dict[str, Any] = {
            "pricingDetails": {
                "recurring": {
                    "interval": "EVERY_30_DAYS",
                    "price": {"amount": str(normalized_price), "currencyCode": "USD"},
                }
            }
        }

        variables: Dict[str, Any] = {
            "name": plan_name,
            "returnUrl": return_url,
            "plans": [recurring_plan],
            "test": bool(test_mode),
        }

        logger.info(
            "Creating Shopify subscription for %s with variables: %s",
            self.store_domain,
            variables,
        )

        try:
            payload = self.graphql(_APP_SUBSCRIPTION_CREATE_MUTATION, variables)
        except ShopifyGraphQLError:
            logger.exception(
                "Shopify GraphQL billing mutation failed for %s.", self.store_domain
            )
            raise

        result = (payload.get("data") or {}).get("appSubscriptionCreateV2") or {}
        user_errors = result.get("userErrors") or []
        if user_errors:
            logger.warning(
                "Shopify returned userErrors during subscription creation for %s: %s",
                self.store_domain,
                user_errors,
            )
            raise ShopifyBillingError(_stringify_graphql_errors(user_errors))

        subscription = result.get("appSubscription") or {}
        confirmation_url = result.get("confirmationUrl") or subscription.get(
            "confirmationUrl", "",
        )

        if not subscription:
            raise ShopifyBillingError(
                "Shopify did not return a subscription record when creating billing."
            )

        if not confirmation_url:
            logger.warning(
                "Shopify subscription created for %s without confirmation URL.",
                self.store_domain,
            )

        return {
            "appSubscription": subscription,
            "confirmationUrl": confirmation_url,
        }

    def search_products(
        self, query: Optional[str], *, cursor: Optional[str] = None, limit: int = 20
    ) -> Dict[str, Any]:
        """Search products using the Admin API GraphQL ``products`` connection."""

        variables = {"query": query or None, "cursor": cursor, "pageSize": limit}
        payload = self.graphql(_PRODUCT_SEARCH_QUERY, variables)
        return _parse_products_response(payload)

    def list_products(
        self, *, cursor: Optional[str] = None, limit: int = 20
    ) -> Dict[str, Any]:
        """Return a paginated list of products without a search query."""

        variables = {"cursor": cursor, "pageSize": limit}
        payload = self.graphql(_PRODUCTS_QUERY, variables)
        return _parse_products_response(payload)

    def get_products_by_ids(self, product_ids: Iterable[str]) -> List[Dict[str, Any]]:
        """Return a list of product details for the given numeric Shopify IDs."""

        ids = [_build_product_gid(pid) for pid in product_ids if pid]
        if not ids:
            return []

        payload = self.graphql(_PRODUCTS_BY_ID_QUERY, {"ids": ids})
        nodes = payload.get("data", {}).get("nodes") or []

        products: List[Dict[str, Any]] = []
        for node in nodes:
            if node and node.get("__typename") == "Product":
                products.append(_parse_product_node(node))
        return products

    def get_all_products(self):
        """Fetch all products in the store catalog."""
        products: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            page = self.list_products(cursor=cursor, limit=50)
            products.extend(page.get("products", []))

            page_info = page.get("pageInfo", {}) or {}
            cursor = page_info.get("endCursor")

            if not page_info.get("hasNextPage") or not cursor:
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


def _build_product_gid(product_id: str) -> str:
    """Return the Shopify GID for a numeric product ID."""

    if str(product_id).startswith("gid://"):
        return str(product_id)
    return f"gid://shopify/Product/{product_id}"


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

    featured_image = node.get("featuredImage") or {}

    return {
        "id": _parse_shopify_gid(node.get("id")),
        "title": node.get("title"),
        "status": node.get("status"),
        "handle": node.get("handle"),
        "onlineStoreUrl": node.get("onlineStoreUrl"),
        "productType": node.get("productType"),
        "featuredImage": {"src": featured_image.get("url") or featured_image.get("originalSrc")},
        "variants": variants,
        "images": images,
    }


def _parse_products_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data", {}) or {}
    products_conn = data.get("products") or {}
    edges = products_conn.get("edges") or []
    page_info = products_conn.get("pageInfo") or {}

    products: List[Dict[str, Any]] = []
    last_cursor = None
    for edge in edges:
        last_cursor = edge.get("cursor") or last_cursor
        node = edge.get("node") or {}
        products.append(_parse_product_node(node))

    return {
        "products": products,
        "pageInfo": {
            "hasNextPage": bool(page_info.get("hasNextPage")),
            "endCursor": page_info.get("endCursor") or last_cursor,
        },
    }


def _parse_money_value(value):
    """Return a simple money amount from a Shopify money field."""

    if isinstance(value, dict):
        return value.get("amount")
    return value


def _stringify_graphql_errors(value: Any) -> str:
    """Return a string representation of nested GraphQL errors for logging."""

    if isinstance(value, dict):
        parts = []
        for key, inner in value.items():
            nested = _stringify_graphql_errors(inner)
            if nested:
                parts.append(f"{key}: {nested}")
        return "; ".join(parts)

    if isinstance(value, (list, tuple, set)):
        parts = [_stringify_graphql_errors(item) for item in value]
        return "; ".join(part for part in parts if part)

    return str(value)


_APP_SUBSCRIPTION_CREATE_MUTATION = """
mutation AppSubscriptionCreate(
  $name: String!,
  $returnUrl: URL!,
  $plans: [AppPlanV2Input!]!,
  $test: Boolean
) {
  appSubscriptionCreateV2(
    name: $name
    returnUrl: $returnUrl
    plans: $plans
    test: $test
  ) {
    confirmationUrl
    userErrors {
      field
      message
    }
    appSubscription {
      id
      status
      confirmationUrl
      lines {
        id
        plan {
          pricingDetails {
            __typename
            ... on AppRecurringPricing {
              interval
              price { amount currencyCode }
            }
          }
        }
      }
    }
  }
}
"""


_PRODUCTS_QUERY = """
query getProducts($cursor: String, $pageSize: Int) {
  products(first: $pageSize, after: $cursor) {
    edges {
      cursor
      node {
        id
        title
        productType
        status
        handle
        onlineStoreUrl
        variants(first: 50) {
          edges {
            node {
              id
              title
              price
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

_PRODUCT_SEARCH_QUERY = """
query searchProducts($query: String, $cursor: String, $pageSize: Int) {
  products(first: $pageSize, query: $query, after: $cursor) {
    edges {
      cursor
      node {
        id
        title
        productType
        status
        handle
        onlineStoreUrl
        featuredImage {
          url
          originalSrc
        }
        variants(first: 20) {
          edges {
            node {
              id
              title
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

_PRODUCTS_BY_ID_QUERY = """
query getProductsById($ids: [ID!]!) {
  nodes(ids: $ids) {
    __typename
    ... on Product {
      id
      title
      status
      handle
      onlineStoreUrl
      featuredImage {
        url
        originalSrc
      }
      variants(first: 20) {
        edges {
          node {
            id
            title
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
}
"""
