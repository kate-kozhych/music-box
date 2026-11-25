from django.apps import AppConfig


class DiaryConfig(AppConfig):
    name = "musicbox_core.apps.diary"
    label = "diary"
    verbose_name = "Listening diary"

    def ready(self) -> None:
        from musicbox_core.apps.catalog import album_page
        from musicbox_core.apps.diary import selectors
        from musicbox_core.apps.diary.erasure import erase_diary
        from musicbox_core.apps.users.erasure import add_erase_step

        album_page.add_viewer_part(selectors.viewer_diary)
        add_erase_step(erase_diary)
