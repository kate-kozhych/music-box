"""Public URLs for files the gateway serves from Identity's media volume.

Lives in `kernel` because every slice that renders an account (accounts,
administration) needs the key → URL mapping, while only `avatars` owns the
upload rules and the storage port (architecture.md §3.2).
"""

from __future__ import annotations

AVATAR_URL_PREFIX = "/media/avatars/"


def avatar_url(key: str | None) -> str | None:
    return f"{AVATAR_URL_PREFIX}{key}" if key else None
