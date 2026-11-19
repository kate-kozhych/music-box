"""Upload rules for avatars: accepted formats, size limit and key generation."""

from __future__ import annotations

import re
import uuid

from identity.kernel.media import AVATAR_URL_PREFIX, avatar_url
from musicbox_common.errors import file_too_large, unsupported_media_type

MAX_BYTES = 2 * 1024 * 1024
URL_PREFIX = AVATAR_URL_PREFIX
KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")

JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"

EXTENSIONS = {JPEG: "jpg", PNG: "png", WEBP: "webp"}

url_for = avatar_url


def detect_media_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    raise unsupported_media_type("Only JPEG, PNG and WebP images are accepted.")


def ensure_within_limit(data: bytes) -> None:
    if len(data) > MAX_BYTES:
        raise file_too_large(MAX_BYTES)


def new_key(media_type: str) -> str:
    return f"{uuid.uuid4().hex}.{EXTENSIONS[media_type]}"
