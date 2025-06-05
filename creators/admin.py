from django.contrib import admin
from .models import CreatorMeta

@admin.register(CreatorMeta)
class CreatorMetaAdmin(admin.ModelAdmin):
    list_display = ('user',)
    search_fields = ('user__username',)
