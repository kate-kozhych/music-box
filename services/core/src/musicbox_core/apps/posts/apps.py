from django.apps import AppConfig


class PostsConfig(AppConfig):
    name = "musicbox_core.apps.posts"
    label = "posts"
    verbose_name = "Music posts"

    def ready(self) -> None:
        from musicbox_core.apps.posts.erasure import erase_posts
        from musicbox_core.apps.users.erasure import add_erase_step

        add_erase_step(erase_posts)
