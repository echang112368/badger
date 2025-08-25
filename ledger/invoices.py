import uuid
from datetime import timedelta
from decimal import Decimal
import os

import requests
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model

from .models import LedgerEntry, MerchantInvoice
from merchants.models import MerchantMeta
from .payouts import _get_paypal_access_token

"""PayPal invoice integration using the sandbox API."""

# Sandbox endpoint for creating and sending invoices. Switch to the live
# endpoint when running in production.
PAYPAL_INVOICE_URL = "https://api-m.sandbox.paypal.com/v2/invoicing/invoices"

# PayPal email that issues the invoices. This should be the business account
# configured in your PayPal developer dashboard.
PAYPAL_INVOICER_EMAIL = os.environ.get("PAYPAL_INVOICER_EMAIL")

# All invoices are issued in USD.
PAYPAL_CURRENCY_CODE = "USD"


def generate_invoice_number() -> str:
    """Return a unique, 25-character invoice number for PayPal."""
    date_str = timezone.now().strftime("%Y%m%d")
    return f"{date_str}-{uuid.uuid4().hex[:16]}"


def create_invoice_for_merchant(merchant):
    """Create and send a PayPal invoice for all unpaid ledger entries."""
    entries = (
        LedgerEntry.objects.filter(merchant=merchant, paid=False, invoice__isnull=True)
        .order_by("id")
    )
    if not entries.exists():
        return None

    meta = MerchantMeta.objects.filter(user=merchant).first()
    if not meta or not meta.paypal_email:
        return None

    if not PAYPAL_INVOICER_EMAIL:
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
            "email_address": PAYPAL_INVOICER_EMAIL,
            "name": {"given_name": "Badger"},
        },
        "primary_recipients": [{"billing_info": {"email_address": meta.paypal_email}}],
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

    detail_resp = requests.get(f"{PAYPAL_INVOICE_URL}/{invoice_id}", headers=headers)
    detail_resp.raise_for_status()
    detail = detail_resp.json()
    pay_url = None
    for link in detail.get("links", []):
        if link.get("rel") == "payer_view":
            pay_url = link.get("href")
            break

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
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(
        f"{PAYPAL_INVOICE_URL}/{invoice.paypal_invoice_id}", headers=headers
    )
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    pay_url = invoice.paypal_invoice_url
    for link in data.get("links", []):
        if link.get("rel") == "payer_view":
            pay_url = link.get("href")
            break

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

    User = get_user_model()
    merchants = User.objects.filter(is_merchant=True)

    created = []
    for merchant in merchants:
        invoice = create_invoice_for_merchant(merchant)
        if invoice:
            created.append(invoice)
    return created
