
### merchants/admin.py
from django.contrib import admin
from .models import MerchantMeta

@admin.register(MerchantMeta)
class MerchantMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'affiliate_percent', 'paypal_email', 'uuid')
    search_fields = ('user__username', 'company_name', 'uuid', 'paypal_email')
    actions = ['generate_invoice']

    @admin.action(description="Generate PayPal Invoice")
    def generate_invoice(self, request, queryset):
        from ledger.invoices import create_invoice_for_merchant

        count = 0
        for meta in queryset:
            invoice = create_invoice_for_merchant(meta.user)
            if invoice:
                count += 1
        self.message_user(request, f"Generated {count} invoice(s)")
