from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        from . import self_deactivation  # noqa: F401  (connects the sign-in signal)
