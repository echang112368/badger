from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = [
        "username",
        "email",
        "is_active",
        "is_creator",
        "is_merchant",
        "is_default_badger_creator",
        "is_staff",
    ]
    list_editable = ["is_active"]
    list_filter = UserAdmin.list_filter + ("is_default_badger_creator",)

    fieldsets = UserAdmin.fieldsets + (
        (
            "Badger Roles",
            {
                "fields": (
                    "is_merchant",
                    "is_creator",
                    "is_default_badger_creator",
                )
            },
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Badger Roles",
            {
                "classes": ("wide",),
                "fields": (
                    "is_merchant",
                    "is_creator",
                    "is_default_badger_creator",
                ),
            },
        ),
    )

