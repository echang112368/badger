from unittest.mock import patch, MagicMock, ANY
from datetime import datetime, timedelta
from django.test import TestCase
from django.urls import reverse
from accounts.models import CustomUser
from merchants.models import MerchantMeta
import uuid
from rest_framework_simplejwt.tokens import RefreshToken

class CreateDiscountViewTests(TestCase):
    def setUp(self):
        user = CustomUser.objects.create_user(
            username="merchant",
            password="pass",
            email="merchant@example.com",
        )
        self.meta = MerchantMeta.objects.create(
            user=user,
            shopify_access_token="token",
            shopify_store_domain="example.myshopify.com",
        )
        self.token = str(RefreshToken.for_user(user).access_token)

    @patch("shopify_app.views.uuid.uuid4")
    @patch("shopify_app.views.ShopifyClient")
    def test_creates_discount_code(self, mock_client_cls, mock_uuid4):
        mock_uuid4.return_value = uuid.UUID("12345678123456781234567812345678")
        mock_client = mock_client_cls.return_value

        price_rule_response = MagicMock()
        price_rule_response.json.return_value = {"price_rule": {"id": 111}}
        discount_response = MagicMock()
        discount_response.json.return_value = {"discount_code": {"code": "BADGER-12345678"}}
        mock_client.post.side_effect = [price_rule_response, discount_response]

        with patch("shopify_app.views.print") as mock_print:
            url = reverse("create_discount", args=[self.meta.uuid])
            response = self.client.post(
                url, HTTP_AUTHORIZATION=f"Bearer {self.token}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"coupon_code": "BADGER-12345678"})
        mock_print.assert_called_once_with("BADGER-12345678")

        first_call = mock_client.post.call_args_list[0]
        rule_payload = first_call.kwargs["json"]["price_rule"]
        self.assertEqual(rule_payload["value"], "-3.0")
        start = datetime.fromisoformat(rule_payload["starts_at"])
        end = datetime.fromisoformat(rule_payload["ends_at"])
        self.assertEqual(end - start, timedelta(days=1))

        mock_client.post.assert_any_call(
            "/admin/api/2024-07/price_rules/111/discount_codes.json",
            json=ANY,
        )
