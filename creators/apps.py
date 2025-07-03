from django.apps import AppConfig


class CreatorsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'creators'

    def ready(self):
        import creators.signals  # noqa: F401
