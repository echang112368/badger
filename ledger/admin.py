from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .models import LedgerEntry, MerchantInvoice
from .payouts import send_mass_payouts
from .invoices import generate_all_invoices


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "creator",
        "merchant",
        "amount",
        "entry_type",
        "timestamp",
        "paid",
    )
    list_filter = ("entry_type", "timestamp", "paid")

    change_list_template = "admin/ledger/ledgerentry/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("send-payouts/", self.admin_site.admin_view(self.send_payouts), name="ledger_send_payouts"),
            path("generate-invoices/", self.admin_site.admin_view(self.generate_invoices), name="ledger_generate_invoices"),
        ]
        return custom_urls + urls

    def send_payouts(self, request):
        if request.method == "POST":
            result = send_mass_payouts(ignore_date=True)
            messages.success(request, f"Sent payouts for {len(result)} creators.")
        return redirect("../")

    def generate_invoices(self, request):
        if request.method == "POST":
            try:
                invoices = generate_all_invoices(ignore_date=True)
            except RuntimeError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Generated {len(invoices)} invoice(s)")
        return redirect("../")


@admin.register(MerchantInvoice)
class MerchantInvoiceAdmin(admin.ModelAdmin):
    """Admin interface for managing merchant invoices."""

    list_display = (
        "id",
        "merchant",
        "status",
        "total_amount",
        "due_date",
        "paypal_invoice_id",
        "created_at",
    )
    list_filter = ("status", "due_date", "created_at")
    search_fields = ("merchant__username", "paypal_invoice_id")
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)
    change_list_template = "admin/ledger/merchantinvoice/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "generate-all/",
                self.admin_site.admin_view(self.generate_invoices),
                name="ledger_invoice_generate_all",
            ),
        ]
        return custom_urls + urls

    def generate_invoices(self, request):
        if request.method == "POST":
            try:
                invoices = generate_all_invoices(ignore_date=True)
            except RuntimeError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Generated {len(invoices)} invoice(s) for merchants with outstanding balances.",
                )
        return redirect("../")

