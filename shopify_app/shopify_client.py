import logging

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

    def get_all_products(self):
        """Fetch all products in the store catalog."""
        products = []
        params = {"limit": 250}
        since_id = None
        while True:
            if since_id:
                params["since_id"] = since_id
            data = self.get("/admin/api/2024-07/products.json", params=params)
            batch = data.get("products", [])
            if not batch:
                break
            products.extend(batch)
            if len(batch) < 250:
                break
            since_id = batch[-1]["id"]
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
