from django.contrib import admin

from .models import InstagramConnection


@admin.register(InstagramConnection)
class InstagramConnectionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "instagram_username",
        "instagram_user_id",
        "platform",
        "followers_count",
        "media_count",
        "last_synced_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "instagram_username",
        "instagram_user_id",
    )
    list_select_related = ("user",)
