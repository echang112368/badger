import uuid
from datetime import timedelta
from decimal import Decimal
import os
from typing import Optional

import requests
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
from django.contrib.auth import get_user_model

from .models import LedgerEntry, MerchantInvoice
from merchants.models import MerchantMeta

from .payouts import _get_paypal_access_token

"""PayPal invoice integration using the sandbox API."""

# Sandbox endpoint for creating and sending invoices. Switch to the live
# endpoint when running in production.
PAYPAL_INVOICE_URL = "https://api-m.sandbox.paypal.com/v2/invoicing/invoices"

# All invoices are issued in USD.
PAYPAL_CURRENCY_CODE = "USD"


def _get_invoice_detail(invoice_id: str, access_token: Optional[str] = None) -> dict:
    """Return the JSON payload for a PayPal invoice."""

    if not invoice_id:
        raise ValueError("invoice_id is required")

    token = access_token or _get_paypal_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{PAYPAL_INVOICE_URL}/{invoice_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def _build_paypal_invoice_url(invoice_id: str) -> Optional[str]:
    """Return the PayPal payer URL derived from the invoice ID."""

    if not invoice_id:
        return None
    return f"https://www.paypal.com/invoice/p/#{invoice_id}"


def generate_invoice_number() -> str:
    """Return a unique, 25-character invoice number for PayPal."""
    date_str = timezone.now().strftime("%Y%m%d")
    return f"{date_str}-{uuid.uuid4().hex[:16]}"


def _get_paypal_invoicer_email() -> str:
    """Return the configured PayPal invoicer email if available."""

    email = getattr(settings, "PAYPAL_INVOICER_EMAIL", None)
    if email:
        return email.strip()

    email = os.environ.get("PAYPAL_INVOICER_EMAIL")
    return email.strip() if email else ""


def create_invoice_for_merchant(merchant):
    """Create and send a PayPal invoice for all unpaid ledger entries."""
    entries = (
        LedgerEntry.objects.filter(merchant=merchant, paid=False, invoice__isnull=True)
        .order_by("id")
    )
    if not entries.exists():
        return None

    meta = MerchantMeta.objects.filter(user=merchant).first()
    paypal_email = (meta.paypal_email or "").strip() if meta else ""
    if not paypal_email:
        return None

    invoicer_email = _get_paypal_invoicer_email()
    if not invoicer_email:
        raise RuntimeError("PAYPAL_INVOICER_EMAIL is not configured")

    # Merchant ledger entries store commissions as negative amounts since the
    # merchant owes money. PayPal invoices expect a positive value, so flip the
    # sign when summing unpaid entries.
    total = -sum((e.amount for e in entries), Decimal("0"))
    if total <= 0:
        return None

    access_token = _get_paypal_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        # Ask PayPal to return the created invoice in the response body so we
        # can immediately access the ID.
        "Prefer": "return=representation",
    }

    payload = {
        "detail": {
            "invoice_number": generate_invoice_number(),
            "currency_code": PAYPAL_CURRENCY_CODE,
        },
        "invoicer": {
            "email_address": invoicer_email,
            "name": {"given_name": "Badger"},
        },
        "primary_recipients": [{"billing_info": {"email_address": paypal_email}}],
        "items": [
            {
                "name": "Monthly charges",
                "quantity": "1",
                "unit_amount": {
                    "currency_code": PAYPAL_CURRENCY_CODE,
                    "value": str(total.quantize(Decimal("0.01"))),
                },
            }
        ],
    }

    response = requests.post(PAYPAL_INVOICE_URL, json=payload, headers=headers)
    response.raise_for_status()
    invoice_data = response.json()
    invoice_id = invoice_data.get("id")
    if not invoice_id:
        # When the Prefer header is not honoured, the ID may only be present in
        # the Location header. Extract it as a fallback to avoid sending a
        # request with "None" in the URL.
        location = response.headers.get("Location", "")
        invoice_id = location.rsplit("/", 1)[-1] if location else None
    if not invoice_id:
        raise RuntimeError("Failed to determine PayPal invoice id")

    send_resp = requests.post(f"{PAYPAL_INVOICE_URL}/{invoice_id}/send", headers=headers)
    send_resp.raise_for_status()

    pay_url = _build_paypal_invoice_url(invoice_id)

    with transaction.atomic():
        invoice = MerchantInvoice.objects.create(
            merchant=merchant,
            paypal_invoice_id=invoice_id,
            paypal_invoice_url=pay_url,
            status="SENT",
            due_date=timezone.now().date() + timedelta(days=14),
            total_amount=total,
        )
        entries.update(invoice=invoice)
    return invoice


def update_invoice_status(invoice: MerchantInvoice):
    """Refresh invoice status from PayPal and mark entries paid if needed."""
    if not invoice.paypal_invoice_id:
        return invoice.status
    access_token = _get_paypal_access_token()
    data = _get_invoice_detail(invoice.paypal_invoice_id, access_token)

    status = data.get("status")
    pay_url = _build_paypal_invoice_url(invoice.paypal_invoice_id) or invoice.paypal_invoice_url

    update_fields = []
    if status and status != invoice.status:
        invoice.status = status
        update_fields.append("status")
        if status == "PAID":
            LedgerEntry.objects.filter(invoice=invoice).update(paid=True)

    if pay_url and pay_url != invoice.paypal_invoice_url:
        invoice.paypal_invoice_url = pay_url
        update_fields.append("paypal_invoice_url")

    if update_fields:
        invoice.save(update_fields=update_fields)

    return invoice.status


def generate_due_invoices():
    """Create invoices for merchants whose join day matches today."""
    today = timezone.now().date()
    User = get_user_model()
    merchants = User.objects.filter(is_merchant=True, date_joined__day=today.day)
    created = []
    for merchant in merchants:
        invoice = create_invoice_for_merchant(merchant)
        if invoice:
            created.append(invoice)
    return created


def generate_all_invoices(ignore_date: bool = False):
    """Generate invoices for all merchants with unpaid ledger entries."""
    today = timezone.now().date()
    if not ignore_date and today.day != 1:
        return []

    outstanding_totals = (
        LedgerEntry.objects.filter(merchant__isnull=False, paid=False, invoice__isnull=True)
        .values("merchant")
        .annotate(total=Sum("amount"))
    )

    merchant_ids = [
        record["merchant"]
        for record in outstanding_totals
        if record["merchant"] is not None and record["total"] and record["total"] < 0
    ]

    if not merchant_ids:
        return []

    User = get_user_model()
    merchants = (
        User.objects.filter(id__in=merchant_ids, is_merchant=True)
        .filter(merchantmeta__paypal_email__isnull=False)
        .exclude(merchantmeta__paypal_email__exact="")
    )

    created = []
    for merchant in merchants:
        invoice = create_invoice_for_merchant(merchant)
        if invoice:
            created.append(invoice)
    return created
