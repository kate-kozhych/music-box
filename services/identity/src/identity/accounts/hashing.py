from __future__ import annotations

import asyncio

from argon2 import PasswordHasher as _Argon2
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    def __init__(self, *, time_cost: int, memory_cost_kib: int, parallelism: int) -> None:
        self._argon = _Argon2(
            time_cost=time_cost,
            memory_cost=memory_cost_kib,
            parallelism=parallelism,
            type=Type.ID,
        )
        self._dummy_hash = self._argon.hash("dummy-password-for-timing-0")

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._argon.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(self._argon.verify, password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    async def verify_dummy(self, password: str) -> None:
        await self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        return self._argon.check_needs_rehash(password_hash)
