from django.apps import AppConfig


class SocialConfig(AppConfig):
    name = "musicbox_core.apps.social"
    label = "social"
    verbose_name = "Social graph (follows)"

    def ready(self) -> None:
        from musicbox_core.apps.social.erasure import erase_follows
        from musicbox_core.apps.users.erasure import add_erase_step

        add_erase_step(erase_follows)
