from django.apps import AppConfig


class FeedConfig(AppConfig):
    name = "musicbox_core.apps.feed"
    label = "feed"
    verbose_name = "Unified feed"

    def ready(self) -> None:
        from musicbox_core.apps.diary.events import (
            DiaryEntryCreated,
            DiaryVisibilityChanged,
        )
        from musicbox_core.apps.feed import services
        from musicbox_core.apps.platform.content import ContentVisibilityChanged
        from musicbox_core.apps.platform.domain_events import subscribe
        from musicbox_core.apps.posts.events import PostAlbumChanged, PostCreated
        from musicbox_core.apps.ratings.events import RatingSet
        from musicbox_core.apps.reviews.events import ReviewCreated

        subscribe(PostCreated, services.post_created)
        subscribe(PostAlbumChanged, services.post_album_changed)
        subscribe(ReviewCreated, services.review_created)
        subscribe(RatingSet, services.rating_set)
        subscribe(DiaryEntryCreated, services.diary_entry_created)
        subscribe(DiaryVisibilityChanged, services.diary_visibility_changed)
        subscribe(ContentVisibilityChanged, services.content_visibility_changed)
