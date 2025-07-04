
### merchants/admin.py
from django.contrib import admin
from .models import MerchantMeta

@admin.register(MerchantMeta)
class MerchantMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name', 'business_ID')
    search_fields = ('user__username', 'company_name', 'business_ID')
