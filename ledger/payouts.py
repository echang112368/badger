import os
import uuid
from decimal import Decimal
from datetime import date
from typing import Dict, List

import requests
from django.db import transaction
from django.db.models import Sum
from django.contrib.auth import get_user_model

from creators.models import CreatorMeta
from .models import LedgerEntry

PAYPAL_OAUTH_URL = "https://api-m.paypal.com/v1/oauth2/token"
PAYPAL_PAYOUT_URL = "https://api-m.paypal.com/v1/payments/payouts"


def _get_paypal_access_token() -> str:
    """Obtain an access token from PayPal."""
    client_id = os.environ.get("PAYPAL_CLIENT_ID")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("PayPal credentials are not configured")

    response = requests.post(
        PAYPAL_OAUTH_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
    )
    response.raise_for_status()
    return response.json()["access_token"]


def send_mass_payouts(ignore_date: bool = False) -> Dict[str, str]:
    """Send PayPal payouts for all creators with unpaid ledger entries.

    Returns a mapping of creator IDs to payout batch IDs for reference.
    The payouts are only executed on the 15th day of each month unless
    ``ignore_date`` is ``True``.
    """
    # Only run payouts on the 15th of the month unless overridden
    if not ignore_date and date.today().day != 15:
        return {}

    access_token = _get_paypal_access_token()

    # Aggregate unpaid amounts per creator
    unpaid_entries = (
        LedgerEntry.objects.filter(paid=False, creator__isnull=False)
        .values("creator")
        .annotate(total=Sum("amount"))
    )

    payouts: List[Dict[str, str]] = []
    creator_entry_map: Dict[int, List[LedgerEntry]] = {}
    User = get_user_model()

    for record in unpaid_entries:
        creator_id = record["creator"]
        total = record["total"] or Decimal("0")
        if total <= 0:
            continue

        try:
            creator = User.objects.get(id=creator_id)
            meta = CreatorMeta.objects.get(user=creator)
        except (User.DoesNotExist, CreatorMeta.DoesNotExist):
            continue
        if not meta.paypal_email:
            continue

        payouts.append(
            {
                "recipient_type": "EMAIL",
                "receiver": meta.paypal_email,
                "amount": {
                    "currency": "USD",
                    "value": str(total.quantize(Decimal("0.01"))),
                },
                "note": "Creator payout",
                "sender_item_id": str(meta.uuid),
            }
        )

        entries = list(
            LedgerEntry.objects.filter(paid=False, creator=creator).order_by("id")
        )
        creator_entry_map[creator.id] = entries

    if not payouts:
        return {}

    batch_id = str(uuid.uuid4())
    payload = {
        "sender_batch_header": {
            "sender_batch_id": batch_id,
            "email_subject": "You have a payout!",
        },
        "items": payouts,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    response = requests.post(PAYPAL_PAYOUT_URL, json=payload, headers=headers)
    response.raise_for_status()
    batch_response = response.json()

    with transaction.atomic():
        for creator_id, entries in creator_entry_map.items():
            total = sum(entry.amount for entry in entries)
            LedgerEntry.objects.filter(id__in=[e.id for e in entries]).update(paid=True)
            if total > 0:
                LedgerEntry.objects.create(
                    creator_id=creator_id,
                    amount=-total,
                    entry_type="payout",
                    paid=True,
                )

    return {str(cid): batch_response.get("batch_header", {}).get("payout_batch_id") for cid in creator_entry_map.keys()}
