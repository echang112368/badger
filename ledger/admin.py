from django.contrib import admin, messages
from django.forms import modelformset_factory
from django.shortcuts import redirect
from django.urls import path
from django.utils.safestring import mark_safe

from .models import LedgerEntry, MerchantInvoice, MerchantMonthlyFee
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


@admin.register(MerchantMonthlyFee)
class MerchantMonthlyFeeAdmin(admin.ModelAdmin):
    change_list_template = "admin/ledger/merchantmonthlyfee/change_list.html"
    list_display = ("merchant_name", "monthly_fee")
    actions = None

    def get_queryset(self, request):
        return self._monthly_fee_queryset()

    @admin.display(description="Merchant")
    def merchant_name(self, obj):
        return obj.company_name or obj.user.get_full_name() or obj.user.username

    def _monthly_fee_queryset(self):
        return (
            MerchantMonthlyFee.objects.select_related("user")
            .all()
            .order_by("company_name", "user__username")
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "update/",
                self.admin_site.admin_view(self.update_monthly_fees),
                name="ledger_monthly_fees_update",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["monthly_fee_formset"] = self._build_monthly_fee_formset()
        return super().changelist_view(request, extra_context=extra_context)

    def _build_monthly_fee_formset(self, data=None):
        formset_cls = modelformset_factory(
            MerchantMonthlyFee,
            fields=("monthly_fee",),
            extra=0,
        )
        formset = formset_cls(
            data=data,
            queryset=self._monthly_fee_queryset(),
            prefix="monthly_fees",
        )
        for form in formset.forms:
            if "monthly_fee" in form.fields:
                form.fields["monthly_fee"].widget.attrs.setdefault("class", "vTextField")
        return formset

    def update_monthly_fees(self, request):
        if request.method != "POST":
            return redirect("../")

        formset = self._build_monthly_fee_formset(data=request.POST)
        if formset.is_valid():
            updated_instances = formset.save()
            messages.success(
                request,
                f"Updated monthly fees for {len(updated_instances)} merchant(s).",
            )
        else:
            error_messages = []
            for form in formset.forms:
                if not form.errors:
                    continue
                meta = form.instance
                merchant_name = meta.company_name or meta.user.get_full_name() or meta.user.username
                for errors in form.errors.values():
                    for error in errors:
                        error_messages.append(f"{merchant_name}: {error}")

            if error_messages:
                formatted = mark_safe("<br>".join(error_messages))
                messages.error(
                    request,
                    mark_safe(f"Unable to update monthly fees:<br>{formatted}"),
                )
            else:
                messages.error(
                    request,
                    "Unable to update monthly fees. Please correct the highlighted fields and try again.",
                )

        return redirect("../")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

