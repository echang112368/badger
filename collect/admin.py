from django.contrib import admin

from .models import RedirectLink, ReferralVisit


@admin.register(RedirectLink)
class RedirectLinkAdmin(admin.ModelAdmin):
    list_display = ("short_code", "destination_url", "queryParam")
    search_fields = ("short_code", "destination_url")


@admin.register(ReferralVisit)
class ReferralVisitAdmin(admin.ModelAdmin):
    list_display = (
        "creator_uuid",
        "merchant_uuid",
        "merchant_domain",
        "landing_path",
        "created_at",
    )
    search_fields = (
        "creator_uuid",
        "merchant_uuid",
        "merchant_domain",
        "landing_url",
    )
    list_filter = ("merchant_domain", "created_at")
    readonly_fields = ("created_at",)
