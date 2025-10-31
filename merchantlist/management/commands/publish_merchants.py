from django.core.management.base import BaseCommand
from merchantlist.utils import publish_merchant_config


class Command(BaseCommand):
    help = "Publish the merchant whitelist JSON and bump the version"

    def handle(self, *args, **options):
        config, payload = publish_merchant_config()
        merchants = payload.get("merchants", [])

        self.stdout.write(
            self.style.SUCCESS(
                f"Published merchant list version {config.merchant_version} with {len(merchants)} merchants."
            )
        )
