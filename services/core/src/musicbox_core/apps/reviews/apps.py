from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    name = "musicbox_core.apps.reviews"
    label = "reviews"
    verbose_name = "Album reviews"

    def ready(self) -> None:
        from musicbox_core.apps.catalog import album_page
        from musicbox_core.apps.reviews import selectors
        from musicbox_core.apps.reviews.erasure import erase_reviews
        from musicbox_core.apps.users.erasure import add_erase_step

        album_page.add_viewer_part(selectors.viewer_review)
        album_page.add_reviews_part(selectors.recent_album_reviews)
        add_erase_step(erase_reviews)
