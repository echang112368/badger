from django.contrib import admin
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _

from .models import Config, Merchant
from .utils import collect_merchant_domains


@admin.register(Merchant)
class MerchantAdmin(admin.ModelAdmin):
    list_display = ("domain",)
    search_fields = ("domain",)


@admin.register(Config)
class ConfigAdmin(admin.ModelAdmin):
    list_display = ("merchant_version", "updated_at", "merchant_count")
    readonly_fields = ("updated_at", "current_domains")

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
