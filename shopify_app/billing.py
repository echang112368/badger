"""Helpers for integrating with the Shopify Billing API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.conf import settings

from merchants.models import MerchantMeta

from .shopify_client import ShopifyClient


class ShopifyBillingError(RuntimeError):
    """Raised when Shopify billing operations fail."""


@dataclass
class ShopifyBillingConfig:
    """Configuration for creating recurring charges."""

    name: str = "Badger Platform Subscription"
    trial_days: int = 0
    capped_amount: Decimal = Decimal("1000.00")
    terms: str = "Usage charges are billed through the Badger platform."
    test_mode: bool = True

    @classmethod
    def from_settings(cls) -> "ShopifyBillingConfig":
        capped = getattr(settings, "SHOPIFY_BILLING_CAPPED_AMOUNT", None)
        terms = getattr(settings, "SHOPIFY_BILLING_TERMS", None)
        test_mode = getattr(settings, "SHOPIFY_BILLING_TEST_MODE", True)
        trial_days = getattr(settings, "SHOPIFY_BILLING_TRIAL_DAYS", 0)
        name = getattr(settings, "SHOPIFY_BILLING_PLAN_NAME", cls.name)

        capped_amount = cls.capped_amount
        if capped is not None:
            try:
                capped_amount = Decimal(str(capped))
            except (InvalidOperation, ValueError) as exc:
                raise ShopifyBillingError("Invalid SHOPIFY_BILLING_CAPPED_AMOUNT setting") from exc

        return cls(
            name=name,
            trial_days=int(trial_days),
            capped_amount=capped_amount,
            terms=terms or cls.terms,
            test_mode=bool(test_mode),
        )


def _require_shopify_credentials(meta: MerchantMeta) -> ShopifyClient:
    if not meta.shopify_access_token or not meta.shopify_store_domain:
        raise ShopifyBillingError("Missing Shopify credentials for merchant.")
    return ShopifyClient(meta.shopify_access_token, meta.shopify_store_domain)


def _ensure_monthly_fee(meta: MerchantMeta) -> Decimal:
    monthly_fee = getattr(meta, "monthly_fee", None)
    if monthly_fee is None or Decimal(monthly_fee) <= 0:
        raise ShopifyBillingError("A positive monthly fee is required for Shopify billing.")
    return Decimal(monthly_fee)


def _update_meta_from_charge(meta: MerchantMeta, charge: dict) -> None:
    capped_amount = charge.get("capped_amount")
    try:
        capped_value: Optional[Decimal]
        if capped_amount is None:
            capped_value = None
        else:
            capped_value = Decimal(str(capped_amount))
    except (InvalidOperation, ValueError) as exc:
        raise ShopifyBillingError("Shopify returned an invalid capped amount.") from exc

    meta.shopify_recurring_charge_id = str(charge.get("id", ""))
    meta.shopify_billing_status = charge.get("status", "") or ""
    meta.shopify_billing_confirmation_url = charge.get("confirmation_url", "") or ""
    meta.shopify_usage_terms = charge.get("terms", "") or ""
    meta.shopify_usage_capped_amount = capped_value
    meta.save(
        update_fields=[
            "shopify_recurring_charge_id",
            "shopify_billing_status",
            "shopify_billing_confirmation_url",
            "shopify_usage_terms",
            "shopify_usage_capped_amount",
        ]
    )


def create_or_update_recurring_charge(meta: MerchantMeta, *, return_url: str) -> dict:
    """Create a recurring application charge for the merchant.

    If an existing charge is pending, Shopify will respond with a new charge that
    can be accepted by the merchant. The function stores the charge details on
    the ``MerchantMeta`` instance and returns the response payload.
    """

    client = _require_shopify_credentials(meta)
    price = _ensure_monthly_fee(meta)
    config = ShopifyBillingConfig.from_settings()

    payload = {
        "recurring_application_charge": {
            "name": config.name,
            "price": str(price.quantize(Decimal("0.01"))),
            "return_url": return_url,
            "trial_days": config.trial_days,
            "test": config.test_mode,
            "capped_amount": str(config.capped_amount.quantize(Decimal("0.01"))),
            "terms": config.terms,
        }
    }

    response = client.post(
        "/admin/api/2024-07/recurring_application_charges.json",
        json=payload,
    )

    data = response.json()
    charge = data.get("recurring_application_charge")
    if not isinstance(charge, dict):
        raise ShopifyBillingError("Unexpected response from Shopify when creating recurring charge.")

    _update_meta_from_charge(meta, charge)
    return charge


def ensure_active_charge(meta: MerchantMeta) -> None:
    """Ensure the merchant has an active Shopify recurring charge."""

    if not meta.shopify_recurring_charge_id:
        raise ShopifyBillingError("Merchant does not have a Shopify recurring charge.")

    if meta.shopify_billing_status.lower() != "active":
        raise ShopifyBillingError("Shopify recurring charge is not active.")


def create_usage_charge(meta: MerchantMeta, *, amount: Decimal, description: str) -> dict:
    """Create a usage charge for the merchant's active recurring charge."""

    ensure_active_charge(meta)

    try:
        normalized_amount = Decimal(amount)
    except (InvalidOperation, ValueError) as exc:
        raise ShopifyBillingError("Invalid usage charge amount.") from exc

    if normalized_amount <= 0:
        raise ShopifyBillingError("Usage charge amount must be greater than zero.")

    client = _require_shopify_credentials(meta)
    charge_id = meta.shopify_recurring_charge_id

    payload = {
        "usage_charge": {
            "description": description or "Badger usage charge",
            "price": str(normalized_amount.quantize(Decimal("0.01"))),
        }
    }

    response = client.post(
        f"/admin/api/2024-07/recurring_application_charges/{charge_id}/usage_charges.json",
        json=payload,
    )
    data = response.json()
    usage_charge = data.get("usage_charge")
    if not isinstance(usage_charge, dict):
        raise ShopifyBillingError("Unexpected Shopify response when creating usage charge.")

    return usage_charge
