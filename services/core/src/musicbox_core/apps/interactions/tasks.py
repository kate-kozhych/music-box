from __future__ import annotations

from celery import shared_task

from musicbox_core.apps.interactions import counters

REBUILD_COUNTERS = "interactions.rebuild_counters"


@shared_task(name=REBUILD_COUNTERS, ignore_result=True)
def rebuild_counters() -> None:
    counters.rebuild_all()
