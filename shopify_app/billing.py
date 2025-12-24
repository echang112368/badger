"""Shopify billing helpers implemented for the Django application.

The functions in this module interact with the Shopify Admin GraphQL API to
create subscriptions, record usage charges, and validate billing status.
Each API call includes inline documentation links to the relevant Shopify
reference pages.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, Optional, Tuple

from django.conf import settings
from django.utils import timezone

from merchants.models import MerchantMeta
from .oauth import ShopifyOAuthError, refresh_access_token
from .shopify_client import ShopifyClient, ShopifyInvalidCredentialsError


logger = logging.getLogger(__name__)


class ShopifyBillingError(RuntimeError):
    """Raised when a Shopify billing operation fails."""


class ShopifyReauthorizationRequired(ShopifyBillingError):
    """Raised when the merchant must re-run Shopify OAuth to continue billing."""


@dataclass
class ShopifyChargeDetails:
    amount: Optional[Decimal] = None
    currency: str = "USD"
    status: str = ""
    charge_id: Optional[str] = None
    confirmation_url: str = ""
    name: str = ""
    description: str = ""
    raw: Optional[Dict[str, Any]] = None
    usage_terms: str = ""
    capped_amount: Optional[Decimal] = None


def _strip_gid(value: str) -> str:
    if not value:
        return ""
    return value.rsplit("/", 1)[-1]


def _assert_shopify_credentials(meta: MerchantMeta) -> None:
    if not meta.shopify_access_token or not meta.shopify_store_domain:
        raise ShopifyReauthorizationRequired(
            "Shopify access token or store domain missing; reauthorization required."
        )


def _refresh_token_if_possible(meta: MerchantMeta) -> Optional[str]:
    """Attempt to refresh the merchant's Shopify token using their refresh token."""

    refresh_token = (meta.shopify_refresh_token or "").strip()
    if not refresh_token:
        logger.info(
            "No Shopify refresh token available for %s; skipping token refresh.",
            meta.shopify_store_domain,
        )
        return None

    try:
        response = refresh_access_token(meta.shopify_store_domain, refresh_token)
    except ShopifyOAuthError as exc:  # pragma: no cover - network issues
        logger.warning(
            "Failed to refresh Shopify token for %s: %s",
            meta.shopify_store_domain,
            exc,
        )
        return None

    meta.shopify_access_token = response.access_token or meta.shopify_access_token
    if response.refresh_token:
        meta.shopify_refresh_token = response.refresh_token
    meta.save(update_fields=["shopify_access_token", "shopify_refresh_token"])

    logger.info(
        "Refreshed Shopify token for %s via offline token flow.",
        meta.shopify_store_domain,
    )
    return meta.shopify_access_token


def _shopify_client(meta: MerchantMeta) -> ShopifyClient:
    """Build a Shopify client tied to the merchant's credentials."""

    return ShopifyClient(
        meta.shopify_access_token,
        meta.shopify_store_domain,
        refresh_handler=lambda: _refresh_token_if_possible(meta),
    )


def expected_shopify_plan_name(meta: MerchantMeta) -> str:
    """Return the Shopify subscription name for the merchant's selected plan."""

    return f"Badger {meta.billing_plan}"


def _parse_usage_line_item(line_items: Iterable[Dict[str, Any]]) -> Tuple[str, str, Optional[Decimal]]:
    """Return the subscription usage line item ID, terms, and capped amount.

    The Shopify Admin GraphQL ``appSubscriptionCreate`` mutation returns line
    items whose plans expose ``pricingDetails`` fragments for ``AppUsagePricing``.
    https://shopify.dev/docs/api/admin-graphql/latest/mutations/appSubscriptionCreate
    """

    fallback_line_id = ""
    for line_item in line_items or []:
        if not isinstance(line_item, dict):
            continue

        line_id = line_item.get("id") or ""
        plan = line_item.get("plan") or {}
        pricing_details = plan.get("pricingDetails") if isinstance(plan, dict) else None
        terms = ""
        capped_amount: Optional[Decimal] = None

        if not fallback_line_id and line_id:
            fallback_line_id = line_id

        if isinstance(pricing_details, dict):
            typename = (pricing_details.get("__typename") or "").lower()
            terms = pricing_details.get("terms", "") or ""
            capped = pricing_details.get("cappedAmount") or {}
            try:
                amount_value = capped.get("amount")
                if amount_value is not None:
                    capped_amount = Decimal(str(amount_value))
            except (InvalidOperation, TypeError, ValueError):
                capped_amount = None

            if typename == "appusagepricing" or terms or capped_amount is not None:
                if line_id:
                    return line_id, terms, capped_amount

    if fallback_line_id:
        return fallback_line_id, "", None

    return "", "", None


def create_or_update_recurring_charge(
    meta: MerchantMeta,
    *,
    return_url: str,
    trial_days: int = 0,
    test_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create a Shopify recurring subscription for the merchant.

    Uses ``appSubscriptionCreate`` from the Admin GraphQL API.
    https://shopify.dev/docs/api/admin-graphql/latest/mutations/appSubscriptionCreate
    """

    _assert_shopify_credentials(meta)

    monthly_fee = getattr(meta, "monthly_fee", Decimal("0")) or Decimal("0")
    try:
        normalized_fee = Decimal(str(monthly_fee))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ShopifyBillingError("Invalid monthly fee configured for Shopify billing.") from exc

    test_flag = test_mode
    if test_flag is None:
        test_flag = bool(getattr(settings, "SHOPIFY_BILLING_TEST_MODE", True))

    usage_cap = getattr(meta, "shopify_usage_capped_amount", None)
    usage_terms = getattr(meta, "shopify_usage_terms", "")

    client = _shopify_client(meta)
    try:
        result = client.create_app_subscription(
            plan_name=expected_shopify_plan_name(meta),
            price_amount=normalized_fee,
            trial_days=int(trial_days),
            return_url=return_url,
            test_mode=test_flag,
            usage_capped_amount=usage_cap,
            usage_terms=usage_terms,
        )
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(str(exc))

    subscription = result.get("subscription") or {}
    line_items: Iterable[Dict[str, Any]] = subscription.get("lineItems") or []
    if not line_items and subscription.get("id"):
        try:
            refresh_payload = client.graphql(
                _APP_SUBSCRIPTION_STATUS_QUERY, {"id": subscription.get("id")}
            )
            line_items = (
                refresh_payload.get("data", {}).get("appSubscription", {}).get("lineItems")
                or []
            )
        except ShopifyInvalidCredentialsError as exc:
            raise ShopifyReauthorizationRequired(str(exc))

    usage_line_id, terms, capped_amount = _parse_usage_line_item(line_items)
    if not terms:
        terms = usage_terms or ""
    if capped_amount is None and usage_cap is not None:
        try:
            capped_amount = Decimal(str(usage_cap))
        except (InvalidOperation, TypeError, ValueError):
            capped_amount = None

    subscription_status = subscription.get("status", "") or ""
    meta.shopify_recurring_charge_id = _strip_gid(subscription.get("id", ""))
    meta.shopify_billing_status = ""
    meta.shopify_billing_plan = ""
    meta.shopify_billing_verified_at = None
    meta.shopify_billing_confirmation_url = result.get("confirmation_url", "") or ""
    meta.shopify_usage_terms = terms
    meta.shopify_usage_capped_amount = capped_amount
    meta.save(
        update_fields=[
            "shopify_recurring_charge_id",
            "shopify_billing_status",
            "shopify_billing_plan",
            "shopify_billing_verified_at",
            "shopify_billing_confirmation_url",
            "shopify_usage_terms",
            "shopify_usage_capped_amount",
        ]
    )

    logger.info(
        "Created Shopify subscription %s for %s with status %s",
        subscription.get("id"),
        meta.shopify_store_domain,
        subscription_status,
    )

    return {
        "id": meta.shopify_recurring_charge_id,
        "status": subscription_status,
        "confirmation_url": meta.shopify_billing_confirmation_url,
        "usage_line_item_id": _strip_gid(usage_line_id),
        "usage_terms": terms,
        "capped_amount": capped_amount,
    }


def refresh_recurring_charge(meta: MerchantMeta) -> Dict[str, Any]:
    """Refresh subscription status using the ``AppSubscription`` object."""

    # Shopify Admin GraphQL AppSubscription object
    # https://shopify.dev/docs/api/admin-graphql/latest/objects/AppSubscription
    _assert_shopify_credentials(meta)

    if not meta.shopify_recurring_charge_id:
        raise ShopifyBillingError("No Shopify subscription is associated with this merchant.")

    client = _shopify_client(meta)
    variables = {"id": f"gid://shopify/AppSubscription/{meta.shopify_recurring_charge_id}"}
    try:
        payload = client.graphql(_APP_SUBSCRIPTION_STATUS_QUERY, variables)
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(str(exc))

    subscription = payload.get("data", {}).get("appSubscription") or {}
    if not subscription:
        raise ShopifyBillingError("Shopify did not return a subscription record.")

    _, terms, capped_amount = _parse_usage_line_item(subscription.get("lineItems") or [])
    meta.shopify_billing_status = subscription.get("status", "") or ""
    meta.shopify_usage_terms = terms
    meta.shopify_usage_capped_amount = capped_amount
    meta.save(
        update_fields=["shopify_billing_status", "shopify_usage_terms", "shopify_usage_capped_amount"],
    )

    return {
        "id": _strip_gid(subscription.get("id", "")),
        "status": meta.shopify_billing_status,
        "usage_terms": terms,
        "capped_amount": capped_amount,
    }


def refresh_active_subscriptions(
    meta: MerchantMeta,
    *,
    expected_plan_name: str,
) -> Dict[str, Any]:
    """Refresh billing status using currentAppInstallation.activeSubscriptions."""

    _assert_shopify_credentials(meta)
    client = _shopify_client(meta)

    try:
        payload = client.graphql(_ACTIVE_SUBSCRIPTIONS_QUERY)
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(str(exc))

    subscriptions = (
        payload.get("data", {})
        .get("currentAppInstallation", {})
        .get("activeSubscriptions")
        or []
    )
    matched_subscription = None
    for subscription in subscriptions:
        if not isinstance(subscription, dict):
            continue
        if subscription.get("name") == expected_plan_name:
            matched_subscription = subscription
            break

    status = ""
    plan_value = ""
    charge_id = meta.shopify_recurring_charge_id
    if matched_subscription:
        status = matched_subscription.get("status", "") or ""
        plan_value = meta.billing_plan
        charge_id = _strip_gid(matched_subscription.get("id", "")) or charge_id

    meta.shopify_billing_status = status
    meta.shopify_billing_plan = plan_value
    meta.shopify_billing_verified_at = timezone.now()
    if charge_id != meta.shopify_recurring_charge_id:
        meta.shopify_recurring_charge_id = charge_id
        update_fields = [
            "shopify_billing_status",
            "shopify_billing_plan",
            "shopify_billing_verified_at",
            "shopify_recurring_charge_id",
        ]
    else:
        update_fields = [
            "shopify_billing_status",
            "shopify_billing_plan",
            "shopify_billing_verified_at",
        ]
    meta.save(update_fields=update_fields)

    return {
        "status": status,
        "plan": plan_value,
        "subscription": matched_subscription,
    }


def ensure_active_charge(meta: MerchantMeta) -> None:
    """Raise when the merchant does not have an active Shopify subscription."""

    status = (meta.shopify_billing_status or "").lower()
    if (
        not meta.shopify_recurring_charge_id
        or status != "active"
        or meta.shopify_billing_plan != meta.billing_plan
    ):
        raise ShopifyBillingError("Shopify billing is not active for this merchant.")


def create_usage_charge(
    meta: MerchantMeta,
    *,
    amount: Decimal,
    description: str,
) -> ShopifyChargeDetails:
    """Create a usage charge for the active subscription.

    Uses ``appUsageRecordCreate`` from the Admin GraphQL API.
    See: https://shopify.dev/docs/api/admin-graphql/latest/mutations/appUsageRecordCreate
    """

    _assert_shopify_credentials(meta)
    ensure_active_charge(meta)

    try:
        normalized_amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ShopifyBillingError("Invalid usage charge amount supplied.") from exc

    if normalized_amount <= 0:
        raise ShopifyBillingError("Usage charge amount must be positive.")

    client = _shopify_client(meta)

    # Query subscription line items to locate the usage-based plan
    # https://shopify.dev/docs/api/admin-graphql/latest/objects/AppSubscription
    subscription_gid = f"gid://shopify/AppSubscription/{meta.shopify_recurring_charge_id}"
    try:
        subscription_payload = client.graphql(
            _APP_SUBSCRIPTION_STATUS_QUERY, {"id": subscription_gid}
        )
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(str(exc))
    subscription = subscription_payload.get("data", {}).get("appSubscription") or {}
    line_items = subscription.get("lineItems") or []
    usage_line_id, terms, capped_amount = _parse_usage_line_item(line_items)

    if not usage_line_id:
        raise ShopifyBillingError("Shopify subscription does not include a usage pricing component.")

    variables = {
        "subscriptionLineItemId": usage_line_id,
        "description": description,
        "price": {"amount": str(normalized_amount), "currencyCode": "USD"},
    }

    # Create usage record for metered billing
    # https://shopify.dev/docs/api/admin-graphql/latest/mutations/appUsageRecordCreate
    try:
        payload = client.graphql(_APP_USAGE_RECORD_CREATE_MUTATION, variables)
    except ShopifyInvalidCredentialsError as exc:
        raise ShopifyReauthorizationRequired(str(exc))
    record_payload = payload.get("data", {}).get("appUsageRecordCreate") or {}
    user_errors = record_payload.get("userErrors") or []
    if user_errors:
        logger.warning("Shopify usage record returned user errors: %s", user_errors)
        raise ShopifyBillingError("; ".join(err.get("message", "") for err in user_errors))

    usage_record = record_payload.get("appUsageRecord") or {}
    charge_id = _strip_gid(usage_record.get("id", ""))
    amount_payload = usage_record.get("price") or {}
    amount_value = amount_payload.get("amount", normalized_amount)
    currency = amount_payload.get("currencyCode", "USD") or "USD"

    try:
        parsed_amount = Decimal(str(amount_value))
    except (InvalidOperation, TypeError, ValueError):
        parsed_amount = normalized_amount

    return ShopifyChargeDetails(
        charge_id=charge_id,
        amount=parsed_amount,
        currency=currency,
        status="processed",
        name="Usage",
        description=description,
        raw=usage_record,
        usage_terms=terms,
        capped_amount=capped_amount,
    )


_APP_SUBSCRIPTION_STATUS_QUERY = """
query GetSubscription($id: ID!) {
  appSubscription(id: $id) {
    id
    status
    name
    lineItems {
      id
      plan {
        pricingDetails {
          __typename
          ... on AppRecurringPricing {
            interval
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
"""

_ACTIVE_SUBSCRIPTIONS_QUERY = """
query GetActiveSubscriptions {
  currentAppInstallation {
    activeSubscriptions {
      id
      name
      status
    }
  }
}
"""

_APP_USAGE_RECORD_CREATE_MUTATION = """
mutation CreateUsageRecord($subscriptionLineItemId: ID!, $description: String!, $price: MoneyInput!) {
  appUsageRecordCreate(
    subscriptionLineItemId: $subscriptionLineItemId
    description: $description
    price: $price
  ) {
    userErrors { field message }
    appUsageRecord {
      id
      description
      price { amount currencyCode }
    }
  }
}
"""
