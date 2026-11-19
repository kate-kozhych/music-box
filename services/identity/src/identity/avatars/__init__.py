from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

from musicbox_common.errors import file_too_large, unsupported_media_type

log = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
URL_PREFIX = "/media/avatars/"
KEY_PATTERN = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")

JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"

EXTENSIONS = {JPEG: "jpg", PNG: "png", WEBP: "webp"}


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


def url_for(key: str | None) -> str | None:
    return f"{URL_PREFIX}{key}" if key else None


class LocalAvatarStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"Refusing to use an avatar key that the server did not generate: {key!r}"
            )
        return self._root / key

    def _write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, raw_temp = tempfile.mkstemp(dir=path.parent, suffix=".part")
        temp = Path(raw_temp)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temp.chmod(0o644)
            temp.replace(path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    async def save(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self._path(key), data)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def discard(self, key: str | None) -> None:
        if key is None:
            return
        try:
            await self.delete(key)
        except OSError:
            log.warning("Could not remove avatar file", extra={"avatar_key": key}, exc_info=True)
