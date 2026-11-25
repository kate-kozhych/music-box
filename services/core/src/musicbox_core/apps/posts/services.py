from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import AppError, ErrorCode, not_found
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.catalog.selectors import get_album
from musicbox_core.apps.platform.content import ContentType, announce_deletion
from musicbox_core.apps.platform.domain_events import dispatch
from musicbox_core.apps.posts.events import PostCreated
from musicbox_core.apps.posts.models import Post
from musicbox_core.apps.posts.policies import ensure_author, ensure_editable
from musicbox_core.apps.posts.selectors import get_post
from musicbox_core.apps.users.services import lock_active_profile


@transaction.atomic
def create_post(principal: Principal, body: str, album_id: uuid.UUID | None) -> Post:
    album = _album(album_id)
    actor = lock_active_profile(principal)
    post = Post.objects.create(user=actor, album=album, body=body, created_at=timezone.now())
    dispatch(PostCreated(post.id, actor.user_id, album.id if album else None))
    return get_post(str(post.id), principal)


@transaction.atomic
def edit_post(principal: Principal, post_id: str, fields: dict[str, Any]) -> Post:
    post = _own_post(principal, post_id)
    ensure_editable(post)
    if "body" in fields:
        post.body = fields["body"]
    if "album_id" in fields:
        post.album = _album(fields["album_id"])
    if fields:
        post.edited_at = timezone.now()
        post.save(update_fields=["body", "album", "edited_at"])
    return get_post(post_id, principal)


@transaction.atomic
def delete_post(principal: Principal, post_id: str) -> None:
    post = _own_post(principal, post_id)
    announce_deletion(ContentType.POST, post.id)
    post.delete()


def _album(album_id: uuid.UUID | None) -> Album | None:
    if album_id is None:
        return None
    try:
        return get_album(str(album_id))
    except AppError as exc:
        raise AppError(422, ErrorCode.UNKNOWN_ALBUM, "No such album.") from exc


def _own_post(principal: Principal, post_id: str) -> Post:
    visible = get_post(post_id, principal)
    ensure_author(principal, visible)
    lock_active_profile(principal)
    locked = Post.objects.select_for_update().filter(id=visible.id).first()
    if locked is None:
        raise not_found("No such post.")
    return locked
