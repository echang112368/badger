"""Legacy Shopify billing module removed in favor of Node.js implementation.

This stub remains to avoid import errors after the billing system was
replaced. All billing features are now handled by the Node.js module in
`shopify_billing/`. Any calls into this module will raise informative
exceptions.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


class ShopifyBillingError(RuntimeError):
    """Raised when a deprecated billing entry point is used."""


class ShopifyReauthorizationRequired(ShopifyBillingError):
    """Raised when billing should be handled by the new Node.js module."""


@dataclass
class ShopifyChargeDetails:
    """Placeholder charge details for backward compatibility."""

    amount: Optional[Any] = None
    status: str = ""
    charge_id: Optional[str] = None
    confirmation_url: str = ""
    raw: Optional[Dict[str, Any]] = None


_DEPRECATION_MESSAGE = (
    "Shopify billing has been migrated to the Node.js module located in "
    "shopify_billing/. Update callers to use the new implementation."
)


def _raise_deprecated(*_args, **_kwargs):
    raise ShopifyBillingError(_DEPRECATION_MESSAGE)


def create_or_update_recurring_charge(*_args, **_kwargs):
    return _raise_deprecated()


def refresh_recurring_charge(*_args, **_kwargs):
    return _raise_deprecated()


def ensure_active_charge(*_args, **_kwargs):
    return _raise_deprecated()


def create_usage_charge(*_args, **_kwargs):
    return _raise_deprecated()
