from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _

from .models import Config, Merchant
from .utils import collect_merchant_domains, publish_merchant_config


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = (
        "merchant_account",
        "domain",
        "business_type",
        "auto_managed",
    )
    list_filter = ("business_type", "auto_managed")
    search_fields = ("account_name", "account__username", "account__email", "domain")
    readonly_fields = ("auto_managed",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "account",
                    "account_name",
                    "domain",
                    "business_type",
                    "auto_managed",
                )
            },
        ),
    )

    @admin.display(description="Merchant account")
    def merchant_account(self, obj):
        return obj.display_name

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.auto_managed:
            readonly.extend(["account", "account_name", "domain", "business_type"])
        return readonly


@admin.register(Config)
class ConfigAdmin(admin.ModelAdmin):
    list_display = ("merchant_version", "updated_at", "merchant_count")
    readonly_fields = ("updated_at", "current_domains")
    change_form_template = "admin/merchantlist/config/change_form.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/publish/",
                self.admin_site.admin_view(self.publish_view),
                name="merchantlist_config_publish",
            ),
        ]
        return custom_urls + urls

    def publish_view(self, request, object_id, *args, **kwargs):
        if not self.has_change_permission(request):
            raise PermissionDenied

        config = self.get_object(request, object_id)
        if config is None:
            messages.error(request, _("Merchant configuration does not exist."))
            return redirect("admin:merchantlist_config_changelist")

        if request.method != "POST":
            return redirect(
                reverse("admin:merchantlist_config_change", args=(config.pk,))
            )

        try:
            config, payload = publish_merchant_config(config)
        except Exception as exc:  # pragma: no cover - defensive
            messages.error(
                request,
                _("Failed to publish merchant list: %(error)s")
                % {"error": exc},
            )
        else:
            new_count = len(payload.get("new_merchants", []))
            base_message = _(
                "Updated merchant list version %(version)s with %(count)s merchants."
            ) % {
                "version": config.merchant_version,
                "count": len(payload.get("merchants", [])),
            }
            if new_count:
                base_message += " " + _("%(added)s new merchant%(plural)s detected.") % {
                    "added": new_count,
                    "plural": "s" if new_count != 1 else "",
                }
            messages.success(request, base_message)

        return redirect(reverse("admin:merchantlist_config_change", args=(config.pk,)))

    def merchant_count(self, obj):
        return len(collect_merchant_domains())

    merchant_count.short_description = _("Domain count")

    def current_domains(self, obj):
        domains = collect_merchant_domains()
        if not domains:
            return _("No merchant domains available")
        return format_html_join(
            "<br>", "{}", ((domain,) for domain in domains)
        )

    current_domains.short_description = _("Current merchant domains")
