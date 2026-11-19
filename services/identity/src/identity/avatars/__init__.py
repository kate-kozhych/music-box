"""Avatar slice: upload rules, storage port and the local-volume adapter.

The slice sits above `accounts` (architecture.md §3.2): `handlers.py` updates
the account and republishes the profile event. The key → URL mapping that every
slice needs to render an account lives in `kernel.media`, so `accounts` never
has to import this slice.
"""

from identity.avatars.local_storage import LocalAvatarStorage
from identity.avatars.ports import AvatarStorage
from identity.avatars.rules import (
    EXTENSIONS,
    JPEG,
    KEY_PATTERN,
    MAX_BYTES,
    PNG,
    URL_PREFIX,
    WEBP,
    detect_media_type,
    ensure_within_limit,
    new_key,
    url_for,
)

__all__ = [
    "EXTENSIONS",
    "JPEG",
    "KEY_PATTERN",
    "MAX_BYTES",
    "PNG",
    "URL_PREFIX",
    "WEBP",
    "AvatarStorage",
    "LocalAvatarStorage",
    "detect_media_type",
    "ensure_within_limit",
    "new_key",
    "url_for",
]
