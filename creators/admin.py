from django.contrib import admin
from .models import CreatorMeta, GmailOAuthCredential

@admin.register(CreatorMeta)
class CreatorMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'paypal_email')
    search_fields = ('user__username',)


@admin.register(GmailOAuthCredential)
class GmailOAuthCredentialAdmin(admin.ModelAdmin):
    list_display = ("user", "gmail_email", "status", "expires_at", "updated_at")
    search_fields = ("user__username", "user__email", "gmail_email")
    list_filter = ("status",)
    readonly_fields = (
        "user",
        "gmail_email",
        "token_uri",
        "scopes",
        "expires_at",
        "status",
        "last_error",
        "created_at",
        "updated_at",
        "revoked_at",
    )
    exclude = ("access_token", "refresh_token")

    def has_add_permission(self, request):
        return False
