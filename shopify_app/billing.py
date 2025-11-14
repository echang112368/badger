"""Helpers for integrating with the Shopify Billing API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from requests import HTTPError

from django.conf import settings

from merchants.models import MerchantMeta

from .shopify_client import ShopifyClient


class ShopifyBillingError(RuntimeError):
    """Raised when Shopify billing operations fail."""


@dataclass
class ShopifyChargeDetails:
    """Normalised details for a Shopify billing charge."""

    charge_id: str
    amount: Decimal
    currency: str
    status: str
    name: str
    description: str
    raw: dict


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


def _extract_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _build_charge_details(
    charge: dict,
    *,
    fallback_amount: Optional[Decimal] = None,
    default_description: str = "",
) -> ShopifyChargeDetails:
    """Return normalised charge information suitable for invoicing."""

    if not isinstance(charge, dict):
        charge = {}

    amount_candidates = (
        charge.get("price"),
        charge.get("amount"),
        charge.get("balance_used"),
        charge.get("capped_amount"),
    )
    amount_value: Optional[Decimal] = None
    for candidate in amount_candidates:
        amount_value = _extract_decimal(candidate)
        if amount_value is not None:
            break

    if amount_value is None:
        amount_value = fallback_amount or Decimal("0.00")

    currency = charge.get("currency") or charge.get("currency_code") or "USD"
    if isinstance(currency, str):
        currency_value = currency.upper()[:3]
    else:
        currency_value = "USD"

    name = str(charge.get("name", "") or "")
    description = str(
        charge.get("description", "")
        or charge.get("terms", "")
        or default_description
        or ""
    )

    status_value = str(
        charge.get("status", "") or charge.get("billing_on", "") or ""
    )

    return ShopifyChargeDetails(
        charge_id=str(charge.get("id", "")),
        amount=amount_value.quantize(Decimal("0.01")),
        currency=currency_value,
        status=status_value,
        name=name,
        description=description,
        raw=charge,
    )


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

    try:
        response = client.post(
            "/admin/api/2024-07/recurring_application_charges.json",
            json=payload,
        )
    except HTTPError as exc:
        raise ShopifyBillingError(_describe_shopify_http_error(exc)) from exc

    data = response.json()
    charge = data.get("recurring_application_charge")
    if not isinstance(charge, dict):
        raise ShopifyBillingError("Unexpected response from Shopify when creating recurring charge.")

    _update_meta_from_charge(meta, charge)
    return charge


def _describe_shopify_http_error(error: HTTPError) -> str:
    response = getattr(error, "response", None)
    if response is None:
        return "Shopify rejected the billing request. Verify your Shopify billing configuration and try again."

    status_text = str(response.status_code)
    if response.reason:
        status_text = f"{status_text} {response.reason}"

    message = f"Shopify rejected the billing request (HTTP {status_text})."

    details = _extract_shopify_error_details(response)
    if details:
        message = f"{message} Details: {details}."
    else:
        message = f"{message} Check your monthly fee, capped amount, and any pending confirmation in Shopify, then try again."

    request_id = response.headers.get("X-Request-Id")
    if request_id:
        message = f"{message} Shopify request ID: {request_id}."

    return message


def _extract_shopify_error_details(response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    messages = []
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if errors:
            messages.append(_stringify_error_value(errors))

        error_text = payload.get("error") or payload.get("message")
        if error_text:
            messages.append(str(error_text))

    if not messages:
        text = (response.text or "").strip()
        if text:
            messages.append(text.splitlines()[0])

    return "; ".join(filter(None, (msg.strip() for msg in messages if msg)))


def _stringify_error_value(value) -> str:
    if isinstance(value, dict):
        parts = []
        for key, inner in value.items():
            inner_text = _stringify_error_value(inner)
            if inner_text:
                parts.append(f"{key}: {inner_text}")
        return "; ".join(parts)

    if isinstance(value, (list, tuple, set)):
        parts = [_stringify_error_value(item) for item in value]
        return "; ".join(part for part in parts if part)

    return str(value)


def ensure_active_charge(meta: MerchantMeta) -> None:
    """Ensure the merchant has an active Shopify recurring charge."""

    if not meta.shopify_recurring_charge_id:
        raise ShopifyBillingError("Merchant does not have a Shopify recurring charge.")

    if meta.shopify_billing_status.lower() != "active":
        raise ShopifyBillingError("Shopify recurring charge is not active.")


def _fetch_recurring_charge(client: ShopifyClient, charge_id: str) -> dict:
    data = client.get(
        f"/admin/api/2024-07/recurring_application_charges/{charge_id}.json"
    )
    charge = data.get("recurring_application_charge")
    if not isinstance(charge, dict):
        raise ShopifyBillingError(
            "Unexpected Shopify response when fetching recurring charge details."
        )
    return charge


def activate_recurring_charge(
    meta: MerchantMeta, *, charge_id: Optional[str] = None
) -> dict:
    """Activate the merchant's Shopify recurring charge if required."""

    client = _require_shopify_credentials(meta)
    target_charge_id = str(charge_id or meta.shopify_recurring_charge_id or "").strip()
    if not target_charge_id:
        raise ShopifyBillingError("Missing Shopify recurring charge identifier.")

    # Refresh the latest charge details prior to attempting activation so the
    # merchant metadata reflects Shopify's current view of the charge.
    charge = _fetch_recurring_charge(client, target_charge_id)
    _update_meta_from_charge(meta, charge)

    if meta.shopify_billing_status.lower() == "active":
        return charge

    try:
        response = client.post(
            f"/admin/api/2024-07/recurring_application_charges/{target_charge_id}/activate.json",
            json={"recurring_application_charge": {"id": target_charge_id}},
        )
        charge_data = response.json()
    except HTTPError as exc:
        # If Shopify reports that the charge cannot be activated (for example,
        # because it is already active), refresh the charge state and only treat
        # it as an error if the charge still is not active.
        charge = _fetch_recurring_charge(client, target_charge_id)
        _update_meta_from_charge(meta, charge)
        if meta.shopify_billing_status.lower() == "active":
            return charge

        error_message = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                error_message = _stringify_error_value(response.json()) or error_message
            except Exception:  # pragma: no cover - defensive parsing
                error_message = error_message
        raise ShopifyBillingError(error_message) from exc
    else:
        charge = charge_data.get("recurring_application_charge")
        if not isinstance(charge, dict):
            raise ShopifyBillingError(
                "Unexpected Shopify response when activating recurring charge."
            )
        _update_meta_from_charge(meta, charge)

    if meta.shopify_billing_status.lower() != "active":
        raise ShopifyBillingError("Shopify recurring charge is not active.")

    return charge


def create_usage_charge(
    meta: MerchantMeta, *, amount: Decimal, description: str
) -> ShopifyChargeDetails:
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

    return _build_charge_details(
        usage_charge,
        fallback_amount=normalized_amount,
        default_description=description,
    )
