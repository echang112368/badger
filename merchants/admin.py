from django.urls import path
from .views import merchant_dashboard

urlpatterns = [
    path('dashboard/', merchant_dashboard, name='merchant_dashboard'),
]


### merchants/admin.py
from django.contrib import admin
from .models import MerchantMeta

@admin.register(MerchantMeta)
class MerchantMetaAdmin(admin.ModelAdmin):
    list_display = ('user', 'company_name')
    search_fields = ('user__username', 'company_name')
