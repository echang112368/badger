from django.contrib import admin

# Register your models here.

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['username', 'email', 'is_creator', 'is_merchant', 'automatic_creator', 'is_staff']
    list_filter = UserAdmin.list_filter + ('is_merchant', 'is_creator', 'automatic_creator')
    fieldsets = UserAdmin.fieldsets + (
        (
            'Badger Roles',
            {'fields': ('is_merchant', 'is_creator', 'automatic_creator')},
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            'Badger Roles',
            {'classes': ('wide',), 'fields': ('is_merchant', 'is_creator', 'automatic_creator')},
        ),
    )

    
admin.site.register(CustomUser, CustomUserAdmin)

