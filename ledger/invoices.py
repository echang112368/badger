import uuid
from datetime import timedelta
from decimal import Decimal
import os
from collections.abc import Iterator, Sequence
from typing import Optional, List

import requests
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum
from django.contrib.auth import get_user_model
from django.urls import reverse

from shopify_app import billing as shopify_billing

from .models import LedgerEntry, MerchantInvoice
from merchants.models import MerchantMeta



class ShopifyBillingConfirmationRequired(RuntimeError):
    """Raised when a Shopify merchant must confirm their recurring charge."""

    def __init__(self, merchant, meta: MerchantMeta, message: Optional[str] = None):
        self.merchant = merchant
        self.meta = meta
        display_name = _merchant_display_name(merchant)
        default_message = (
            f"Shopify billing confirmation required for {display_name}."
        )
        super().__init__(message or default_message)


class InvoiceGenerationResult(Sequence):
    """Container for invoice generation output and Shopify pending state."""

    def __init__(self, created: List[MerchantInvoice], pending_shopify: List[MerchantMeta]):
        self.created = created
        self.pending_shopify = pending_shopify

    def __len__(self) -> int:
        return len(self.created)

    def __getitem__(self, index):
        return self.created[index]

    def __iter__(self) -> Iterator[MerchantInvoice]:
        return iter(self.created)


def _build_shopify_return_url(request=None) -> str:
    """Best-effort construction of the Shopify billing return URL."""

    path = reverse("merchant_invoices")

    if request is not None:
        return request.build_absolute_uri(path)

    candidates = [
        getattr(settings, "SHOPIFY_APP_URL", ""),
        getattr(settings, "SHOPIFY_APP_ORIGIN", ""),
        getattr(settings, "SITE_URL", ""),
    ]

    for candidate in candidates:
        if candidate:
            base = str(candidate).strip().rstrip("/")
            if base:
                return f"{base}{path}"

    hosts = getattr(settings, "ALLOWED_HOSTS", []) or []
    if hosts:
        host = hosts[0].strip()
        if host:
            scheme = "http" if getattr(settings, "DEBUG", False) else "https"
            return f"{scheme}://{host}{path}"

    raise RuntimeError("Unable to determine Shopify billing return URL. Configure SHOPIFY_APP_URL or provide a request object.")

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


def get_paypal_payment_link(
    invoice_id: str, *, access_token: Optional[str] = None
) -> str:
    """Return the customer-facing PayPal payment link for an invoice.

    The function authenticates with PayPal using the client credentials from the
    environment, fetches the invoice detail payload, and extracts the
    ``recipient_view_url`` value that customers use to pay their invoices.

    Args:
        invoice_id: The PayPal invoice identifier.
        access_token: Optional pre-fetched OAuth token to reuse.

    Raises:
        ValueError: If ``invoice_id`` is not supplied.
        RuntimeError: When PayPal responds with an error or the URL cannot be
            located in the response payload.

    Returns:
        The public, customer-facing PayPal payment URL.
    """

    if not invoice_id:
        raise ValueError("invoice_id is required")

    try:
        data = _get_invoice_detail(invoice_id, access_token=access_token)
    except requests.HTTPError as exc:
        raise RuntimeError(
            f"PayPal returned an error for invoice '{invoice_id}'."
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to retrieve PayPal invoice '{invoice_id}'."
        ) from exc

    try:
        detail = data.get("detail", {})
        metadata = detail.get("metadata", {})
    except AttributeError as exc:
        raise RuntimeError("Unexpected PayPal invoice payload structure.") from exc

    pay_url = metadata.get("recipient_view_url")
    if not pay_url:
        raise RuntimeError("PayPal invoice payload missing recipient_view_url.")

    return pay_url


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


def _merchant_display_name(merchant) -> str:
    meta = None
    try:
        meta = merchant.merchantmeta
    except MerchantMeta.DoesNotExist:
        meta = None

    if meta and meta.company_name:
        return meta.company_name

    full_name = merchant.get_full_name()
    return full_name or merchant.username


def _is_shopify_billed(meta: Optional[MerchantMeta]) -> bool:
    if not meta:
        return False
    if meta.business_type == MerchantMeta.BusinessType.SHOPIFY:
        return True
    return bool(meta.shopify_store_domain and meta.shopify_access_token)


def _billing_cycle_day(merchant, meta: Optional[MerchantMeta]) -> int:
    if _is_shopify_billed(meta) and meta.shopify_billing_verified_at:
        return meta.shopify_billing_verified_at.day
    return merchant.date_joined.day


def create_invoice_for_merchant(merchant):
    """Create and send a PayPal invoice for all unpaid ledger entries."""

    meta = MerchantMeta.objects.filter(user=merchant).first()
    paypal_email = (meta.paypal_email or "").strip() if meta else ""
    shopify_business = _is_shopify_billed(meta)

    if not shopify_business and not paypal_email:
        return None

    invoicer_email = None
    if not shopify_business:
        invoicer_email = _get_paypal_invoicer_email()
        if not invoicer_email:
            raise RuntimeError("PAYPAL_INVOICER_EMAIL is not configured")

    now = timezone.now()
    start_of_period = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _pending_entries():
        return (
            LedgerEntry.objects.filter(
                merchant=merchant, paid=False, invoice__isnull=True
            ).order_by("id")
        )

    entries = _pending_entries()

    monthly_fee = Decimal("0.00")
    affiliate_exists = entries.filter(entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT).exists()

    if meta and meta.monthly_fee and (
        not shopify_business or affiliate_exists or not entries.exists()
    ):
        monthly_fee = meta.monthly_fee.quantize(Decimal("0.01"))
        if monthly_fee > 0:
            has_monthly_fee = entries.filter(
                entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
                amount=-monthly_fee,
                timestamp__gte=start_of_period,
            ).exists()
            if not has_monthly_fee:
                LedgerEntry.objects.create(
                    merchant=merchant,
                    amount=-monthly_fee,
                    entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
                )
                entries = _pending_entries()

    def _sum_positive(qs) -> Decimal:
        total = qs.aggregate(total=Sum("amount")).get("total")
        if total is None:
            return Decimal("0.00")
        return abs(total).quantize(Decimal("0.01"))

    affiliate_total = _sum_positive(
        entries.filter(entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT)
    )

    affiliate_processing_fee = Decimal("0.00")
    if affiliate_total > 0:
        affiliate_processing_fee = (affiliate_total * Decimal("0.05")).quantize(
            Decimal("0.01")
        )
        if affiliate_processing_fee > 0:
            has_processing_fee = entries.filter(
                entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
                amount=-affiliate_processing_fee,
                timestamp__gte=start_of_period,
            ).exists()
            if not has_processing_fee:
                LedgerEntry.objects.create(
                    merchant=merchant,
                    amount=-affiliate_processing_fee,
                    entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
                )
                entries = _pending_entries()

    if not entries.exists():
        return None

    affiliate_total = _sum_positive(
        entries.filter(entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT)
    )
    badger_total = _sum_positive(
        entries.filter(entry_type=LedgerEntry.EntryType.BADGER_PAYOUT)
    )
    other_total = _sum_positive(
        entries.exclude(
            entry_type__in=[
                LedgerEntry.EntryType.AFFILIATE_PAYOUT,
                LedgerEntry.EntryType.BADGER_PAYOUT,
            ]
        )
    )

    affiliate_processing_fee = Decimal("0.00")
    if affiliate_total > 0:
        affiliate_processing_fee = (affiliate_total * Decimal("0.05")).quantize(
            Decimal("0.01")
        )

    monthly_fee_line = Decimal("0.00")
    if (
        monthly_fee > 0
        and entries.filter(
            entry_type=LedgerEntry.EntryType.BADGER_PAYOUT,
            amount=-monthly_fee,
        ).exists()
    ):
        monthly_fee_line = monthly_fee

    badger_payout_total = badger_total
    if monthly_fee_line > 0:
        badger_payout_total -= monthly_fee_line
    if affiliate_processing_fee > 0:
        badger_payout_total -= affiliate_processing_fee
    if badger_payout_total < 0:
        badger_payout_total = Decimal("0.00")

    components = []
    if affiliate_total > 0:
        components.append(("Affiliate payouts", affiliate_total))
    if badger_payout_total > 0:
        components.append(("Badger payouts", badger_payout_total))
    if monthly_fee_line > 0:
        components.append(("Monthly fee", monthly_fee_line))
    if affiliate_processing_fee > 0:
        components.append(("Affiliate processing fee (5%)", affiliate_processing_fee))
    if other_total > 0:
        components.append(("Additional ledger adjustments", other_total))

    total = sum((amount for _, amount in components), Decimal("0.00"))
    if not components:
        total = _sum_positive(entries)
        if total <= 0:
            return None
        components.append(("Outstanding balance", total))
    elif total <= 0:
        return None

    if shopify_business:
        try:
            shopify_billing.ensure_active_charge(meta)
        except shopify_billing.ShopifyBillingError:
            # Initiate or refresh the subscription and ask the merchant to confirm.
            return_url = _build_shopify_return_url()
            shopify_billing.create_or_update_recurring_charge(
                meta,
                return_url=return_url,
            )
            raise ShopifyBillingConfirmationRequired(merchant, meta)

        description = "Badger monthly invoice"
        charge = shopify_billing.create_usage_charge(
            meta,
            amount=total,
            description=description,
        )

        with transaction.atomic():
            invoice = MerchantInvoice.objects.create(
                merchant=merchant,
                provider=MerchantInvoice.Provider.SHOPIFY,
                status=charge.status or "processed",
                due_date=timezone.now().date(),
                total_amount=total.quantize(Decimal("0.01")),
                shopify_charge_id=charge.charge_id,
                shopify_status=charge.status,
                shopify_payload=charge.raw or {},
            )
            entries.update(invoice=invoice, paid=True)

        return invoice

    access_token = _get_paypal_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        # Ask PayPal to return the created invoice in the response body so we
        # can immediately access the ID.
        "Prefer": "return=representation",
    }

    total_amount = total.quantize(Decimal("0.01"))

    items_payload = [
        {
            "name": name,
            "quantity": "1",
            "unit_amount": {
                "currency_code": PAYPAL_CURRENCY_CODE,
                "value": str(amount.quantize(Decimal("0.01"))),
            },
        }
        for name, amount in components
    ]

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
        "items": items_payload,
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

    pay_url: Optional[str] = None
    try:
        pay_url = get_paypal_payment_link(invoice_id, access_token=access_token)
    except RuntimeError:
        pay_url = None

    if not pay_url:
        pay_url = f"https://www.paypal.com/invoice/p/#{invoice_id}"

    with transaction.atomic():
        invoice = MerchantInvoice.objects.create(
            merchant=merchant,
            paypal_invoice_id=invoice_id,
            paypal_invoice_url=pay_url,
            status="SENT",
            due_date=timezone.now().date() + timedelta(days=14),
            total_amount=total_amount,
        )
        entries.update(invoice=invoice)
    return invoice


def update_invoice_status(invoice: MerchantInvoice):
    """Refresh invoice status from PayPal and mark entries paid if needed."""
    if invoice.provider == MerchantInvoice.Provider.SHOPIFY:
        return invoice.status

    if not invoice.paypal_invoice_id:
        return invoice.status
    access_token = _get_paypal_access_token()
    data = _get_invoice_detail(invoice.paypal_invoice_id, access_token)

    status = data.get("status")
    pay_url = invoice.paypal_invoice_url
    try:
        pay_url = get_paypal_payment_link(
            invoice.paypal_invoice_id, access_token=access_token
        )
    except RuntimeError:
        if not pay_url and invoice.paypal_invoice_id:
            pay_url = f"https://www.paypal.com/invoice/p/#{invoice.paypal_invoice_id}"

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
    merchants = User.objects.filter(is_merchant=True).select_related("merchantmeta")
    created = []
    for merchant in merchants:
        meta = getattr(merchant, "merchantmeta", None)
        if _billing_cycle_day(merchant, meta) != today.day:
            continue
        try:
            invoice = create_invoice_for_merchant(merchant)
        except ShopifyBillingConfirmationRequired:
            continue
        if invoice:
            created.append(invoice)
    return created


def generate_all_invoices(ignore_date: bool = False, shopify_only: bool = False):
    """Generate invoices for all merchants with unpaid ledger entries.

    When ``shopify_only`` is true, only Shopify merchants are processed and
    PayPal invoice generation is skipped. This is primarily used by the admin
    interface when triggering Shopify billing directly.

    Returns:
        InvoiceGenerationResult: Created invoices and Shopify merchants still
        pending billing confirmation.
    """
    today = timezone.now().date()
    if not ignore_date and today.day != 1:
        return InvoiceGenerationResult([], [])

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
        return InvoiceGenerationResult([], [])

    User = get_user_model()
    merchants_qs = (
        User.objects.filter(id__in=merchant_ids, is_merchant=True)
        .select_related("merchantmeta")
    )

    merchants: List = list(merchants_qs)
    missing_fees: List[str] = []
    ready_merchants = []
    for merchant in merchants:
        try:
            meta = merchant.merchantmeta
        except MerchantMeta.DoesNotExist:
            meta = None

        if meta is None:
            if shopify_only:
                continue
            missing_fees.append(_merchant_display_name(merchant))
            continue

        fee = getattr(meta, "monthly_fee", None)
        shopify_business = _is_shopify_billed(meta)

        if shopify_only and not shopify_business:
            continue

        if fee is None or fee <= 0:
            if not shopify_only or shopify_business:
                missing_fees.append(_merchant_display_name(merchant))
            continue

        if shopify_business:
            ready_merchants.append(merchant)
            continue

        if shopify_only:
            continue

        email = (meta.paypal_email or "").strip()
        if email:
            ready_merchants.append(merchant)

    if missing_fees:
        missing_fees = sorted(set(missing_fees))
        missing_list = ", ".join(missing_fees)
        raise RuntimeError(
            "Cannot generate invoices because the following merchants do not have a monthly fee configured: "
            f"{missing_list}."
        )

    created = []
    pending_shopify: List[MerchantMeta] = []
    for merchant in ready_merchants:
        try:
            invoice = create_invoice_for_merchant(merchant)
        except ShopifyBillingConfirmationRequired as pending:
            if isinstance(pending.meta, MerchantMeta):
                pending_shopify.append(pending.meta)
            continue

        if invoice:
            created.append(invoice)
    return InvoiceGenerationResult(created, pending_shopify)
