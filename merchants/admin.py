
### merchants/admin.py
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .models import MerchantMeta
from ledger.invoices import ShopifyBillingConfirmationRequired

@admin.register(MerchantMeta)
class MerchantMetaAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'company_name',
        'business_type',
        'billing_plan',
        'shopify_billing_status',
        'monthly_fee',
        'uuid',
    )
    search_fields = ('user__username', 'company_name', 'uuid', 'paypal_email', 'shopify_store_domain')
    list_filter = ('business_type', 'billing_plan', 'shopify_billing_status')
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
            path(
                "refresh-shopify-subscriptions/",
                self.admin_site.admin_view(self.refresh_shopify_subscriptions),
                name="merchants_refresh_shopify_subscriptions",
            ),
        ]
        return custom_urls + urls

    @admin.action(description="Generate PayPal Invoice")
    def generate_invoice(self, request, queryset):
        from ledger.invoices import create_invoice_for_merchant

        count = 0
        pending_merchants = []
        for meta in queryset:
            try:
                invoice = create_invoice_for_merchant(meta.user)
            except ShopifyBillingConfirmationRequired as pending:
                pending_merchants.append(pending.meta)
                continue

            if invoice:
                count += 1

        if pending_merchants:
            names = ", ".join(
                sorted(
                    {
                        meta.company_name or meta.user.get_full_name() or meta.user.username
                        for meta in pending_merchants
                        if meta and getattr(meta, "user", None)
                    }
                )
            )
            self.message_user(
                request,
                (
                    "Shopify billing confirmation required for: "
                    f"{names}. Confirmation links are available on each merchant's invoices page."
                ),
                level=messages.WARNING,
            )

        self.message_user(request, f"Generated {count} invoice(s)")

    def generate_invoices(self, request):
        if request.method == "POST":
            from ledger.invoices import generate_all_invoices

            try:
                result = generate_all_invoices(ignore_date=True)
            except RuntimeError as exc:
                messages.error(request, str(exc))
            else:
                if result.pending_shopify:
                    names = ", ".join(
                        sorted(
                            {
                                meta.company_name
                                or meta.user.get_full_name()
                                or meta.user.username
                                for meta in result.pending_shopify
                                if meta and getattr(meta, "user", None)
                            }
                        )
                    )
                    messages.warning(
                        request,
                        (
                            "Shopify billing confirmation required for: "
                            f"{names}. Confirmation links are available on each merchant's invoices page."
                        ),
                    )

                messages.success(request, f"Generated {len(result)} invoice(s)")
        return redirect("../")

    def refresh_shopify_subscriptions(self, request):
        from shopify_app import billing as shopify_billing

        changelist = self.get_changelist_instance(request)
        queryset = changelist.get_queryset(request)
        shopify_merchants = (
            queryset.filter(business_type=MerchantMeta.BusinessType.SHOPIFY)
            .select_related("user")
        )
        refreshed = 0
        reauth_required = []
        failures = []
        for meta in shopify_merchants:
            try:
                shopify_billing.refresh_active_subscriptions(
                    meta,
                    expected_plan_name=shopify_billing.expected_shopify_plan_name(meta),
                )
            except shopify_billing.ShopifyReauthorizationRequired:
                name = meta.company_name or meta.user.get_full_name() or meta.user.username
                reauth_required.append(name)
            except shopify_billing.ShopifyBillingError:
                name = meta.company_name or meta.user.get_full_name() or meta.user.username
                failures.append(name)
            else:
                refreshed += 1

        if refreshed:
            messages.success(
                request,
                f"Refreshed Shopify subscriptions for {refreshed} merchant(s).",
            )
        if reauth_required:
            names = ", ".join(sorted(set(reauth_required)))
            messages.warning(
                request,
                f"Shopify reauthorization required for: {names}.",
            )
        if failures:
            names = ", ".join(sorted(set(failures)))
            messages.error(
                request,
                f"Failed to refresh Shopify subscriptions for: {names}.",
            )
        if not refreshed and not reauth_required and not failures:
            messages.info(request, "No Shopify merchants found to refresh.")

        return redirect(request.META.get("HTTP_REFERER", "../"))
