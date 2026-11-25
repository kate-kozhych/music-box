from django.apps import AppConfig


class InteractionsConfig(AppConfig):
    name = "musicbox_core.apps.interactions"
    label = "interactions"
    verbose_name = "Comments and likes"

    def ready(self) -> None:
        from musicbox_core.apps.interactions import counters, erasure, selectors, services
        from musicbox_core.apps.platform import engagement
        from musicbox_core.apps.platform.content import ContentDeleting
        from musicbox_core.apps.platform.domain_events import subscribe
        from musicbox_core.apps.users.erasure import add_erase_step
        from musicbox_core.apps.users.events import UserVisibilityChanged

        engagement.add_liked_lookup(selectors.liked_ids)
        subscribe(ContentDeleting, services.purge_interactions)
        subscribe(UserVisibilityChanged, counters.rebuild_touched_by)
        add_erase_step(erasure.erase_events)
        add_erase_step(erasure.erase_comments)
        add_erase_step(erasure.erase_likes)
