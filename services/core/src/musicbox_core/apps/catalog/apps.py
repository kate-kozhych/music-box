from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "musicbox_core.apps.catalog"
    label = "catalog"
    verbose_name = "Music catalog (MusicBrainz release groups, artists, genres)"
