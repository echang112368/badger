
### merchants/admin.py
from django.contrib import admin
from .models import MerchantMeta

@admin.register(MerchantMeta)
class MerchantMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'affiliate_percent', 'uuid')
    search_fields = ('user__username', 'company_name', 'uuid')
