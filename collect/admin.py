from django.contrib import admin

from .models import AffiliateClick, RedirectLink, ReferralVisit, ReferralConversion


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


@admin.register(ReferralConversion)
class ReferralConversionAdmin(admin.ModelAdmin):
    list_display = (
        "creator_uuid",
        "merchant_uuid",
        "order_id",
        "order_amount",
        "commission_amount",
        "created_at",
    )
    search_fields = (
        "creator_uuid",
        "merchant_uuid",
        "order_id",
    )
    list_filter = ("created_at",)
    readonly_fields = ("created_at",)


@admin.register(AffiliateClick)
class AffiliateClickAdmin(admin.ModelAdmin):
    list_display = ("uuid", "storeID", "created_at")
    search_fields = ("uuid", "storeID")
    list_filter = ("created_at",)
    readonly_fields = ("created_at",)
