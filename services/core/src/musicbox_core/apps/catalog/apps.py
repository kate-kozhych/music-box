from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "musicbox_core.apps.catalog"
    label = "catalog"
    verbose_name = "Music catalog (MusicBrainz release groups, artists, genres)"

    def ready(self) -> None:
        from musicbox_core.apps.catalog.health import musicbrainz_status
        from musicbox_core.apps.platform.views import register_probe

        register_probe("musicbrainz", musicbrainz_status)
