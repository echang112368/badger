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

"""
for live website use https://api-m.paypal.com/v2/invoicing/invoices 
Currently using sandbox url
"""
PAYPAL_INVOICE_URL = "https://api-m.sandbox.paypal.com/v2/invoicing/invoices"

# Email address of the PayPal account sending invoices. This should match
# the business account configured in your PayPal settings.
PAYPAL_INVOICER_EMAIL = os.environ.get("PAYPAL_INVOICER_EMAIL")
# All invoices are issued in a single currency.
PAYPAL_CURRENCY_CODE = "USD"


def generate_invoice_number() -> str:
    """Return a unique, 25-character invoice number for PayPal."""
    date_str = timezone.now().strftime("%Y%m%d")
    return f"{date_str}-{uuid.uuid4().hex[:16]}"




def create_invoice_for_merchant(merchant):
    """Create and send a PayPal invoice for all unpaid ledger entries."""
    entries = LedgerEntry.objects.filter(
        merchant=merchant, paid=False, invoice__isnull=True
    ).order_by("id")
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
        "primary_recipients": [
            {"billing_info": {"email_address": meta.paypal_email}}
        ],
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

    send_resp = requests.post(
        f"{PAYPAL_INVOICE_URL}/{invoice_id}/send", headers=headers
    )
    send_resp.raise_for_status()

    detail = requests.get(
        f"{PAYPAL_INVOICE_URL}/{invoice_id}", headers=headers
    ).json()
    pay_url = ""
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
    data = requests.get(
        f"{PAYPAL_INVOICE_URL}/{invoice.paypal_invoice_id}", headers=headers
    ).json()
    status = data.get("status")
    if status and status != invoice.status:
        invoice.status = status
        invoice.save(update_fields=["status"])
        if status == "PAID":
            LedgerEntry.objects.filter(invoice=invoice).update(paid=True)
    return status


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
    """Generate invoices for all merchants with unpaid ledger entries.

    By default this only runs on the first day of the month, mirroring the
    scheduled cron behaviour. Set ``ignore_date`` to ``True`` to bypass this
    restriction and create invoices immediately.
    """
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
