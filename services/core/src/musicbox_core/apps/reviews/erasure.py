from __future__ import annotations

import uuid

from django.db import transaction

from musicbox_core.apps.platform.content import ContentType, announce_deletion
from musicbox_core.apps.reviews.models import Review

BATCH_SIZE = 100


@transaction.atomic
def erase_reviews(user_id: uuid.UUID) -> int:
    reviews = Review.objects.filter(user_id=user_id)
    ids = list(reviews.select_for_update().order_by("id").values_list("id", flat=True)[:BATCH_SIZE])
    for content_id in ids:
        announce_deletion(ContentType.REVIEW, content_id)
    reviews.filter(id__in=ids).delete()
    return len(ids)
