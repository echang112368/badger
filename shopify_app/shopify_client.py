import requests
from requests.auth import HTTPBasicAuth


class ShopifyClient:
    """Helper for making authenticated requests to a Shopify store."""

    def __init__(self, api_key: str, password: str, store_domain: str):
        self.api_key = api_key
        self.password = password
        self.store_domain = store_domain.rstrip('/')

    def request(self, method: str, path: str, **kwargs):
        url = f"https://{self.store_domain}{path}"
        response = requests.request(
            method, url, auth=HTTPBasicAuth(self.api_key, self.password), **kwargs
        )
        response.raise_for_status()
        return response

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)
