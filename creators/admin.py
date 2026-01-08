from django.contrib import admin
from .models import CreatorMeta

@admin.register(CreatorMeta)
class CreatorMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'display_name', 'paypal_email', 'primary_platform', 'follower_count')
    search_fields = ('user__username',)
