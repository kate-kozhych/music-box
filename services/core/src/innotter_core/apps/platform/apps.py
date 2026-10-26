from django.apps import AppConfig


class PlatformConfig(AppConfig):
    name = "innotter_core.apps.platform"
    label = "platform"
    verbose_name = "Platform (auth, outbox/inbox, cross-cutting)"

    def ready(self) -> None:
        from innotter_core.apps.platform import schema  # noqa: F401
