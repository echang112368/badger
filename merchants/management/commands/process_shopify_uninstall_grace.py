from django.core.management.base import BaseCommand
from django.utils import timezone

from merchants.models import MerchantMeta


class Command(BaseCommand):
    help = "Deactivate and release Shopify associations whose uninstall grace period expired."

    def handle(self, *args, **options):
        now = timezone.now()
        released = 0

        queryset = MerchantMeta.objects.select_related("user").filter(
            shopify_uninstalled_at__isnull=False,
        )

        for meta in queryset.iterator():
            if meta.process_shopify_uninstall_grace_expiration(now=now):
                released += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed Shopify uninstall grace expirations. Released {released} account(s)."
            )
        )
