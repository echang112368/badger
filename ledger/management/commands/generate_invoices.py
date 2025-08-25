from django.core.management.base import BaseCommand

from ledger.invoices import generate_due_invoices

class Command(BaseCommand):
    help = "Generate PayPal invoices for merchants due today"

    def handle(self, *args, **options):
        invoices = generate_due_invoices()
        self.stdout.write(self.style.SUCCESS(f"Generated {len(invoices)} invoice(s)"))
