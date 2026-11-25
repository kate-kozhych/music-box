from __future__ import annotations

from typing import Any


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.expiry: dict[str, float] = {}
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _expire_due(self) -> None:
        for key in [k for k, at in self.expiry.items() if at <= self.now]:
            self.store.pop(key, None)
            self.expiry.pop(key, None)

    def set(
        self,
        name: str,
        value: bytes,
        *,
        nx: bool = False,
        px: int | None = None,
        ex: int | None = None,
    ) -> bool | None:
        self._expire_due()
        if nx and name in self.store:
            return None
        self.store[name] = value
        if px is not None:
            self.expiry[name] = self.now + px / 1000
        elif ex is not None:
            self.expiry[name] = self.now + ex
        return True

    def get(self, name: str) -> bytes | None:
        self._expire_due()
        return self.store.get(name)

    def incr(self, name: str) -> int:
        self._expire_due()
        value = int(self.store.get(name, b"0")) + 1
        self.store[name] = str(value).encode()
        return value

    def expire(self, name: str, seconds: int) -> bool:
        if name not in self.store:
            return False
        self.expiry[name] = self.now + seconds
        return True

    def exists(self, *names: str) -> int:
        self._expire_due()
        return sum(1 for name in names if name in self.store)

    def delete(self, *names: str) -> int:
        removed = 0
        for name in names:
            removed += 1 if self.store.pop(name, None) is not None else 0
            self.expiry.pop(name, None)
        return removed

    def __getattr__(self, item: str) -> Any:  # pragma: no cover
        raise AttributeError(f"FakeRedis does not implement {item!r}")
