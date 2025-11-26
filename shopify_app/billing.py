"""Helpers for integrating with the Shopify Billing API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from requests import HTTPError

from django.conf import settings

from merchants.models import MerchantMeta

from .oauth import normalise_shop_domain
from .shopify_client import (
    ShopifyClient,
    ShopifyInvalidCredentialsError,
    _parse_shopify_gid,
)
from .token_management import refresh_shopify_token


logger = logging.getLogger(__name__)


class ShopifyBillingError(RuntimeError):
    """Raised when Shopify billing operations fail."""


class ShopifyReauthorizationRequired(ShopifyBillingError):
    """Raised when Shopify rejects billing requests due to invalid tokens."""

    def __init__(self, shop_domain: str, message: str = ""):
        self.shop_domain = normalise_shop_domain(shop_domain)
        message = message or (
            "Shopify rejected the billing request because the stored credentials are invalid."
        )
        super().__init__(message)


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
    logger.info(
        "Initialising ShopifyClient with access token for %s: %s",
        meta.shopify_store_domain,
        meta.shopify_access_token,
    )
    if getattr(meta, "shopify_refresh_token", ""):
        logger.info(
            "Using Shopify refresh token for %s: %s",
            meta.shopify_store_domain,
            meta.shopify_refresh_token,
        )
    return ShopifyClient(
        meta.shopify_access_token,
        meta.shopify_store_domain,
        refresh_handler=lambda: refresh_shopify_token(meta),
    )


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

    variables = {
        "name": config.name,
        "returnUrl": return_url,
        "trialDays": config.trial_days,
        "test": config.test_mode,
        "price": str(price.quantize(Decimal("0.01"))),
        "cappedAmount": str(config.capped_amount.quantize(Decimal("0.01"))),
        "terms": config.terms,
    }

    try:
        payload = client.graphql(_APP_SUBSCRIPTION_CREATE_MUTATION, variables)
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(meta.shopify_store_domain) from exc
    except HTTPError as exc:
        raise ShopifyBillingError(_describe_shopify_http_error(exc)) from exc

    result = payload.get("data", {}).get("appSubscriptionCreateV2") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise ShopifyBillingError(_stringify_error_value(user_errors))

    subscription = result.get("appSubscription")
    if not isinstance(subscription, dict):
        raise ShopifyBillingError(
            "Unexpected response from Shopify when creating recurring charge."
        )

    charge = _parse_app_subscription(subscription, confirmation_url=result.get("confirmationUrl"))

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


def refresh_recurring_charge(meta: MerchantMeta) -> dict:
    """Fetch the latest recurring charge from Shopify and update the merchant meta."""

    if not meta.shopify_recurring_charge_id:
        raise ShopifyBillingError("Merchant does not have a Shopify recurring charge.")

    client = _require_shopify_credentials(meta)
    charge_id = meta.shopify_recurring_charge_id

    try:
        subscription = _load_subscription(
            client, _build_subscription_gid(charge_id)
        )
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(meta.shopify_store_domain) from exc
    except HTTPError as exc:
        raise ShopifyBillingError(_describe_shopify_http_error(exc)) from exc

    charge = _parse_app_subscription(subscription)
    _update_meta_from_charge(meta, charge)
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

    subscription_gid = _build_subscription_gid(charge_id)

    try:
        subscription = _load_subscription(client, subscription_gid)
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(meta.shopify_store_domain) from exc
    except HTTPError as exc:
        raise ShopifyBillingError(_describe_shopify_http_error(exc)) from exc

    usage_line_item_id = _extract_usage_line_item_id(subscription)
    if not usage_line_item_id:
        raise ShopifyBillingError(
            "Unable to locate a usage-based line item for the active subscription."
        )

    variables = {
        "subscriptionLineItemId": usage_line_item_id,
        "price": {
            "amount": str(normalized_amount.quantize(Decimal("0.01"))),
            "currencyCode": "USD",
        },
        "description": description or "Badger usage charge",
    }

    payload = client.graphql(_USAGE_RECORD_CREATE_MUTATION, variables)
    result = payload.get("data", {}).get("appUsageRecordCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise ShopifyBillingError(_stringify_error_value(user_errors))

    record = result.get("appUsageRecord") or {}
    usage_charge = {
        "id": _parse_shopify_gid(record.get("id")),
        "price": record.get("price", {}).get("amount"),
        "currency": record.get("price", {}).get("currencyCode"),
        "description": record.get("description", ""),
    }

    return _build_charge_details(
        usage_charge,
        fallback_amount=normalized_amount,
        default_description=description,
    )


def _build_subscription_gid(charge_id: str) -> str:
    charge_value = str(charge_id)
    if charge_value.startswith("gid://"):
        return charge_value
    return f"gid://shopify/AppSubscription/{charge_value}"


def _load_subscription(client: ShopifyClient, subscription_gid: str) -> dict:
    payload = client.graphql(_APP_SUBSCRIPTION_QUERY, {"id": subscription_gid})
    subscription = payload.get("data", {}).get("appSubscription")
    if not isinstance(subscription, dict):
        raise ShopifyBillingError("Shopify did not return a subscription record.")
    return subscription


def _parse_app_subscription(subscription: dict, *, confirmation_url: str = "") -> dict:
    if not isinstance(subscription, dict):
        return {}

    recurring_line = None
    usage_line = None

    # Support both legacy ``lineItems`` responses and the 2024+ ``lines``
    # connection returned by ``appSubscriptionCreateV2``.
    lines = subscription.get("lineItems") or subscription.get("lines") or []
    if isinstance(lines, dict):
        lines = lines.get("edges", []) or []
    for line in lines:
        node = line.get("node") if isinstance(line, dict) else None
        line_value = node if node is not None else line
        plan = (line_value or {}).get("plan", {}) or {}
        pricing = plan.get("pricingDetails") or plan
        typename = pricing.get("__typename", "")
        if typename == "AppRecurringPricing":
            recurring_line = pricing
        elif typename == "AppUsagePricing":
            usage_line = pricing

    price_info = (recurring_line or {}).get("price") or {}
    capped_info = (usage_line or {}).get("cappedAmount") or {}

    return {
        "id": _parse_shopify_gid(subscription.get("id")),
        "status": subscription.get("status"),
        "confirmation_url": confirmation_url or subscription.get("confirmationUrl", ""),
        "terms": (usage_line or {}).get("terms", ""),
        "capped_amount": capped_info.get("amount"),
        "price": price_info.get("amount"),
        "currency": price_info.get("currencyCode") or capped_info.get("currencyCode") or "USD",
    }


def _extract_usage_line_item_id(subscription: dict) -> Optional[str]:
    lines = subscription.get("lineItems") or subscription.get("lines") or []
    if isinstance(lines, dict):
        lines = lines.get("edges", []) or []

    for line in lines:
        node = line.get("node") if isinstance(line, dict) else None
        line_value = node if node is not None else line
        plan = line_value.get("plan", {}) or {}
        pricing = plan.get("pricingDetails") or plan
        if pricing.get("__typename") == "AppUsagePricing" and line_value.get("id"):
            return line_value.get("id")
    return None


_APP_SUBSCRIPTION_QUERY = """
query SubscriptionById($id: ID!) {
  appSubscription(id: $id) {
    id
    status
    confirmationUrl
    lines(first: 10) {
      edges {
        node {
          id
          plan {
            pricingDetails {
              __typename
              ... on AppRecurringPricing {
                interval
                price {
                  amount
                  currencyCode
                }
              }
              ... on AppUsagePricing {
                terms
                cappedAmount {
                  amount
                  currencyCode
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


_APP_SUBSCRIPTION_CREATE_MUTATION = """
mutation CreateSubscription(
  $name: String!
  $returnUrl: URL!
  $trialDays: Int!
  $test: Boolean!
  $price: Decimal!
  $cappedAmount: Decimal!
  $terms: String!
) {
  appSubscriptionCreateV2(
    name: $name
    returnUrl: $returnUrl
    trialDays: $trialDays
    test: $test
    plans: [
      { appRecurringPricingDetails: { interval: EVERY_30_DAYS, price: { amount: $price, currencyCode: USD } } }
      { appUsagePricingDetails: { cappedAmount: { amount: $cappedAmount, currencyCode: USD }, terms: $terms } }
    ]
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
      lines(first: 10) {
        edges {
          node {
            id
            plan {
              pricingDetails {
                __typename
                ... on AppRecurringPricing {
                  price { amount currencyCode }
                }
                ... on AppUsagePricing {
                  terms
                  cappedAmount { amount currencyCode }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


_USAGE_RECORD_CREATE_MUTATION = """
mutation CreateUsageRecord(
  $subscriptionLineItemId: ID!
  $price: MoneyInput!
  $description: String!
) {
  appUsageRecordCreate(
    subscriptionLineItemId: $subscriptionLineItemId
    price: $price
    description: $description
  ) {
    appUsageRecord {
      id
      description
      price {
        amount
        currencyCode
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""
