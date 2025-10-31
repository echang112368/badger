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
    list_display = ("domain",)
    search_fields = ("domain",)


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
            messages.success(
                request,
                _(
                    "Published merchant list version %(version)s with %(count)s merchants."
                )
                % {
                    "version": config.merchant_version,
                    "count": len(payload.get("merchants", [])),
                },
            )

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
