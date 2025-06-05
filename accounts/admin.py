from django.contrib import admin

# Register your models here.

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['username', 'email', 'is_creator', 'is_merchant','is_staff']

    # Let admin edit these fields directly in the user form
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('is_creator', 'is_merchant')}),
    )

    # Also include them when adding a new user
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('is_creator', 'is_merchant')}),
    )

admin.site.register(CustomUser, CustomUserAdmin)

