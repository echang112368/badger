from django.core.management.base import BaseCommand

from ledger.payouts import send_mass_payouts


class Command(BaseCommand):
    help = "Send PayPal payouts for all unpaid ledger entries"

    def handle(self, *args, **options):
        result = send_mass_payouts()
        self.stdout.write(self.style.SUCCESS(f"Sent payouts for {len(result)} creators"))
