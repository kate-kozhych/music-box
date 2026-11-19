"""Local-volume adapter for `AvatarStorage` (NFR-009: S3 is the Could variant)."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from identity.avatars.rules import KEY_PATTERN

log = logging.getLogger(__name__)


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
