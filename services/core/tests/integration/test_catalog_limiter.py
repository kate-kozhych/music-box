from __future__ import annotations

import itertools
import threading
import time
import uuid

import pytest
import redis
from django.conf import settings

from musicbox_core.apps.catalog.throttle import RedisRateLimiter

pytestmark = pytest.mark.integration


def test_concurrent_callers_share_one_slot_per_second() -> None:
    key = f"test:slot:{uuid.uuid4()}"
    granted: list[float] = []

    def call() -> None:
        client = redis.Redis.from_url(settings.MUSICBOX_REDIS_CACHE_URL)
        if RedisRateLimiter(client, key=key).acquire(2.5):
            granted.append(time.monotonic())
        client.close()

    threads = [threading.Thread(target=call) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    granted.sort()
    gaps = [later - earlier for earlier, later in itertools.pairwise(granted)]
    assert len(granted) == 3
    assert all(gap >= 0.9 for gap in gaps)
