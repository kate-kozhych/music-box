from django.apps import AppConfig


class RatingsConfig(AppConfig):
    name = "musicbox_core.apps.ratings"
    label = "ratings"
    verbose_name = "Album ratings"

    def ready(self) -> None:
        from musicbox_core.apps.catalog import album_page
        from musicbox_core.apps.platform.domain_events import subscribe
        from musicbox_core.apps.ratings import selectors, services
        from musicbox_core.apps.users.events import UserVisibilityChanged

        album_page.add_viewer_part(selectors.viewer_rating)
        subscribe(UserVisibilityChanged, services.rebuild_stats_rated_by)
