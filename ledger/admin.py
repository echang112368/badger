from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .models import LedgerEntry
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
            invoices = generate_all_invoices(ignore_date=True)
            messages.success(request, f"Generated {len(invoices)} invoice(s)")
        return redirect("../")

