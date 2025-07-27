from django.contrib import admin
from .models import LedgerEntry

@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "creator",
        "merchant",
        "amount",
        "entry_type",
        "timestamp",
        "paid",
    )
    list_filter = ("entry_type", "timestamp", "paid")
