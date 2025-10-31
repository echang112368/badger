import json
from datetime import timezone as dt_timezone
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from merchantlist.models import Config
from merchantlist.utils import collect_merchant_domains


class Command(BaseCommand):
    help = "Publish the merchant whitelist JSON and bump the version"

    def handle(self, *args, **options):
        merchants = collect_merchant_domains()
        static_path = Path(__file__).resolve().parent.parent.parent / "static" / "merchant_list.json"
        static_path.parent.mkdir(parents=True, exist_ok=True)

        with transaction.atomic():
            config, _ = Config.objects.select_for_update().get_or_create(pk=1)
            config.merchant_version += 1
            config.save(update_fields=["merchant_version", "updated_at"])

            updated_utc = timezone.localtime(config.updated_at, dt_timezone.utc)
            data = {
                "version": config.merchant_version,
                "updated": updated_utc.isoformat().replace("+00:00", "Z"),
                "merchants": merchants,
            }

            with static_path.open("w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
                fp.write("\n")

        self.stdout.write(
            self.style.SUCCESS(
                f"Published merchant list version {config.merchant_version} with {len(merchants)} merchants."
            )
        )
