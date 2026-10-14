from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = "integrations"

    def ready(self):
        from . import registry

        registry.autodiscover()
