from django.contrib import admin

from creatorMatch.integrations.models import (
    IntegrationSyncRun,
    OAuthState,
    SocialAccount,
    SocialAccountToken,
    SocialMetricSnapshot,
)


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "provider",
        "username",
        "connection_status",
        "last_sync_status",
        "last_synced_at",
    )
    list_filter = ("provider", "connection_status", "last_sync_status")
    search_fields = ("user__username", "username", "external_account_id")


@admin.register(SocialAccountToken)
class SocialAccountTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "social_account", "expires_at", "invalidated_at")


@admin.register(IntegrationSyncRun)
class IntegrationSyncRunAdmin(admin.ModelAdmin):
    list_display = ("id", "social_account", "status", "started_at", "finished_at")


@admin.register(SocialMetricSnapshot)
class SocialMetricSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "social_account", "provider", "captured_at")


@admin.register(OAuthState)
class OAuthStateAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "provider", "state", "expires_at", "consumed_at")
    search_fields = ("user__username", "state")
