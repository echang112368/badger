from django.contrib import admin
from .models import MerchantCreatorLink, MerchantUser

class CreatorInline(admin.TabularInline):
    model = MerchantCreatorLink
    fk_name = 'merchant'
    extra = 1

@admin.register(MerchantUser)
class MerchantUserAdmin(admin.ModelAdmin):
    list_display = ('username', 'email')
    inlines = [CreatorInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(is_merchant=True)

    def has_module_permission(self, request):
        return True

    def has_view_permission(self, request, obj=None):
        return True
