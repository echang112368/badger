import requests


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

        if response.status_code == 401 and callable(self._refresh_handler):
            new_token = self._refresh_handler()
            if new_token and new_token != self.access_token:
                self.access_token = new_token
                retry_headers = dict(base_headers)
                retry_headers["X-Shopify-Access-Token"] = self.access_token
                response = send(retry_headers)

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
