from __future__ import annotations

import uuid
from typing import Protocol

from pydantic import BaseModel


class Outbox(Protocol):
    def add(self, event_type: str, payload: BaseModel) -> None: ...
    async def anonymize_user(self, user_id: uuid.UUID, username: str) -> None: ...
