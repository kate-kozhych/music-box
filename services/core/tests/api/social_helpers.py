from __future__ import annotations

from typing import Any

from django.utils import timezone

from musicbox_core.apps.platform.models import OutboxEvent
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.users.models import UserProfile


def make_post(user: UserProfile, **fields: Any) -> Post:
    return Post.objects.create(user=user, **{"body": "Hi", "created_at": timezone.now(), **fields})


def hide(content: Any) -> None:
    type(content).objects.filter(pk=content.pk).update(
        hidden_at=timezone.now(), hidden_reason="spam"
    )


def url(content: Any, suffix: str = "") -> str:
    return f"/api/v1/{type(content).__name__.lower()}s/{content.id}{suffix}"


def events(event_type: str) -> list[dict[str, Any]]:
    return list(
        OutboxEvent.objects.filter(type=event_type)
        .order_by("event_id")
        .values_list("payload", flat=True)
    )
