from __future__ import annotations

from celery import shared_task

from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.ratings import services

REBUILD_ALBUM_STATS = "ratings.rebuild_album_stats"


@shared_task(name=REBUILD_ALBUM_STATS, ignore_result=True)
def rebuild_album_stats() -> None:
    services.rebuild_stats(Album.objects.values_list("id", flat=True))
