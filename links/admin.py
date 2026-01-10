from django.contrib import admin
from .models import MerchantCreatorLink, PartnershipRequest

class CreatorInline(admin.TabularInline):
    model = MerchantCreatorLink
    fk_name = 'merchant'
    extra = 1

class MerchantInline(admin.TabularInline):
    model = MerchantCreatorLink
    fk_name = 'creator'
    extra = 1

# Do NOT register the user model here again to avoid AlreadyRegistered error
# Instead, define reusable inlines that can be imported and used inside accounts/admin.py

# Only register the relationship model directly for visibility
@admin.register(MerchantCreatorLink)
class MerchantCreatorLinkAdmin(admin.ModelAdmin):
    list_display = ('merchant', 'creator', 'status')
    search_fields = ('merchant__username', 'creator__username')
    list_filter = ('status',)


@admin.register(PartnershipRequest)
class PartnershipRequestAdmin(admin.ModelAdmin):
    list_display = ("creator", "merchant", "status", "created_at")
    search_fields = ("creator__username", "merchant__username", "message")
    list_filter = ("status", "created_at")

# Now in accounts/admin.py you can do:
# from links.admin import CreatorInline, MerchantInline
# and use them inside your CustomUserAdmin
