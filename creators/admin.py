from django.contrib import admin
from .models import CreatorMeta, GmailOAuthCredential, OutreachAgentInteraction, OutreachDraft, OutreachThreadSummary

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


@admin.register(OutreachDraft)
class OutreachDraftAdmin(admin.ModelAdmin):
    list_display = ("creator", "business", "recipient_email", "status", "updated_at", "sent_at")
    search_fields = ("creator__username", "recipient_email", "subject")
    list_filter = ("status", "tone")


@admin.register(OutreachThreadSummary)
class OutreachThreadSummaryAdmin(admin.ModelAdmin):
    list_display = ("creator", "business", "gmail_thread_id", "updated_at")
    search_fields = ("creator__username", "gmail_thread_id", "summary")


@admin.register(OutreachAgentInteraction)
class OutreachAgentInteractionAdmin(admin.ModelAdmin):
    list_display = ("creator", "business", "action_type", "created_at")
    search_fields = ("creator__username", "error_message")
    list_filter = ("action_type",)
    readonly_fields = ("creator", "business", "action_type", "safe_input", "structured_output", "error_message", "created_at")

    def has_add_permission(self, request):
        return False
