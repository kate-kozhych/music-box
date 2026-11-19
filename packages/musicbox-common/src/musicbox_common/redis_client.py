"""One typed entry point for the async Redis clients the services build.

`redis.asyncio.from_url` ships without annotations, so calling it directly makes
every composition root fail `mypy --strict` with `no-untyped-call`. Wrapping it
here keeps the suppression to a single, documented boundary instead of scattering
`type: ignore` comments through the services.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

__all__ = ["Redis", "async_client"]


def async_client(url: str, **kwargs: Any) -> aioredis.Redis:
    client: aioredis.Redis = aioredis.from_url(url, **kwargs)  # type: ignore[no-untyped-call]
    return client
