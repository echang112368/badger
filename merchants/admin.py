
### merchants/admin.py
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .models import MerchantMeta

@admin.register(MerchantMeta)
class MerchantMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'paypal_email', 'monthly_fee', 'uuid')
    search_fields = ('user__username', 'company_name', 'uuid', 'paypal_email')
    actions = ['generate_invoice']
    change_list_template = "admin/merchants/merchantmeta/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "generate-invoices/",
                self.admin_site.admin_view(self.generate_invoices),
                name="merchants_generate_invoices",
            ),
        ]
        return custom_urls + urls

    @admin.action(description="Generate PayPal Invoice")
    def generate_invoice(self, request, queryset):
        from ledger.invoices import create_invoice_for_merchant

        count = 0
        for meta in queryset:
            invoice = create_invoice_for_merchant(meta.user)
            if invoice:
                count += 1
        self.message_user(request, f"Generated {count} invoice(s)")

    def generate_invoices(self, request):
        if request.method == "POST":
            from ledger.invoices import generate_all_invoices

            try:
                invoices = generate_all_invoices(ignore_date=True)
            except RuntimeError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Generated {len(invoices)} invoice(s)")
        return redirect("../")
