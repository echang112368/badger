from __future__ import annotations

import json

from django.test import TestCase
from django.urls import reverse

from merchantlist import views
from merchantlist.models import Config, Merchant
from merchantlist.utils import publish_merchant_config


class MerchantListPublishTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.static_file = views.STATIC_FILE
        self._original_static_contents: str | None = None
        if self.static_file.exists():
            self._original_static_contents = self.static_file.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        if self._original_static_contents is None:
            if self.static_file.exists():
                self.static_file.unlink()
        else:
            self.static_file.write_text(self._original_static_contents, encoding="utf-8")
        super().tearDown()

    def test_publish_updates_api_response_for_non_default_config_pk(self) -> None:
        config = Config.objects.create(pk=10, merchant_version=7)
        Merchant.objects.create(domain="example.com")

        config, payload = publish_merchant_config(config)

        response = self.client.get(reverse("merchant_list"))
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content)
        self.assertEqual(data["version"], config.merchant_version)
        self.assertEqual(data["merchants"], payload["merchants"])
        self.assertEqual(data["updated"], payload["updated"])

    def test_publish_updates_meta_endpoint(self) -> None:
        config = Config.objects.create(pk=5, merchant_version=2)
        config, _ = publish_merchant_config(config)

        response = self.client.get(reverse("merchant_meta"))
        self.assertEqual(response.status_code, 200)

        data = json.loads(response.content)
        self.assertEqual(data["version"], config.merchant_version)
        self.assertIn("updated", data)
