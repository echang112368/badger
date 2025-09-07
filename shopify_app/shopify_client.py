import requests


class ShopifyClient:
    """Helper for making authenticated requests to a Shopify store."""

    def __init__(self, access_token: str, store_domain: str):
        self.access_token = access_token
        self.store_domain = store_domain.rstrip('/')

    def request(self, method: str, path: str, **kwargs):
        url = f"https://{self.store_domain}{path}"
        headers = kwargs.pop("headers", {})
        headers["X-Shopify-Access-Token"] = self.access_token
        headers.setdefault("Content-Type", "application/json")
        response = requests.request(method, url, headers=headers, **kwargs)
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
