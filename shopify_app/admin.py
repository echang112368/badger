"""Admin registrations for Shopify integration models."""

import json

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path
from django.utils.html import format_html

from . import billing as shopify_billing
from .models import ShopifyChargeRecord, Shop


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ("shop_domain",)
    search_fields = ("shop_domain",)


@admin.register(ShopifyChargeRecord)
class ShopifyChargeRecordAdmin(admin.ModelAdmin):
    list_display = (
        "processed_at",
        "merchant",
        "charge_type",
        "amount",
        "currency",
        "status",
        "shopify_charge_id",
        "summary",
    )
    list_filter = ("charge_type", "currency", "status")
    search_fields = (
        "merchant__username",
        "merchant__email",
        "merchant_meta__company_name",
        "shopify_charge_id",
        "description",
    )
    readonly_fields = (
        "merchant",
        "merchant_meta",
        "charge_type",
        "shopify_charge_id",
        "name",
        "description",
        "amount",
        "currency",
        "status",
        "processed_at",
        "formatted_response",
    )
    ordering = ("-processed_at",)
    change_list_template = "admin/shopify_app/shopifychargerecord/change_list.html"

    fieldsets = (
        (None, {"fields": ("merchant", "merchant_meta", "charge_type", "status", "processed_at")}),
        ("Charge details", {"fields": ("shopify_charge_id", "name", "description", "amount", "currency")}),
        ("Shopify payload", {"fields": ("formatted_response",)}),
    )

    def get_urls(self):  # pragma: no cover - thin wrapper
        urls = super().get_urls()
        custom_urls = [
            path(
                "send-billing/",
                self.admin_site.admin_view(self.send_shopify_billing),
                name="shopify_app_shopifychargerecord_send_billing",
            ),
        ]
        return custom_urls + urls

    def has_add_permission(self, request):  # pragma: no cover - admin only
        return False

    def has_change_permission(self, request, obj=None):  # pragma: no cover - admin only
        return False

    def has_delete_permission(self, request, obj=None):  # pragma: no cover - admin only
        return False

    def send_shopify_billing(self, request):
        from ledger.invoices import generate_all_invoices

        if request.method != "POST":
            messages.error(request, "Shopify billing can only be triggered via POST.")
            return redirect("../")

        before_count = ShopifyChargeRecord.objects.count()
        try:
            generate_all_invoices(ignore_date=True, shopify_only=True)
        except shopify_billing.ShopifyBillingError as exc:
            messages.error(request, str(exc))
        except RuntimeError as exc:
            messages.error(request, str(exc))
        else:
            after_count = ShopifyChargeRecord.objects.count()
            created = max(0, after_count - before_count)
            if created:
                messages.success(
                    request,
                    f"Triggered {created} Shopify billing charge{'s' if created != 1 else ''}.",
                )
            else:
                messages.info(request, "No Shopify billing charges were created.")
        return redirect("../")

    @admin.display(description="Summary")
    def summary(self, obj: ShopifyChargeRecord):
        return obj.short_description

    @admin.display(description="Shopify payload")
    def formatted_response(self, obj: ShopifyChargeRecord):
        if not obj.raw_response:
            return ""
        pretty = json.dumps(obj.raw_response, indent=2, sort_keys=True)
        return format_html("<pre>{}</pre>", pretty)
