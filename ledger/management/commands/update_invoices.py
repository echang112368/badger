from django.core.management.base import BaseCommand

from ledger.models import MerchantInvoice
from ledger.invoices import update_invoice_status

class Command(BaseCommand):
    help = "Update PayPal invoice statuses"

    def handle(self, *args, **options):
        count = 0
        for invoice in MerchantInvoice.objects.exclude(status="PAID"):
            update_invoice_status(invoice)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Checked {count} invoice(s)"))
