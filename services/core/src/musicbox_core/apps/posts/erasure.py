from __future__ import annotations

import uuid

from django.db import transaction

from musicbox_core.apps.platform.content import ContentType, announce_deletion
from musicbox_core.apps.posts.models import Post

BATCH_SIZE = 100


@transaction.atomic
def erase_posts(user_id: uuid.UUID) -> int:
    posts = Post.objects.filter(user_id=user_id)
    ids = list(posts.select_for_update().order_by("id").values_list("id", flat=True)[:BATCH_SIZE])
    for content_id in ids:
        announce_deletion(ContentType.POST, content_id)
    posts.filter(id__in=ids).delete()
    return len(ids)
