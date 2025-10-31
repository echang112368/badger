from django.apps import AppConfig


class MerchantlistConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "merchantlist"
    verbose_name = "Merchant List"

    def ready(self) -> None:
        super().ready()
        from . import signals

        signals.register_signals()
