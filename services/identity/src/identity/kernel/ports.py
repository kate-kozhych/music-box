from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class Outbox(Protocol):
    def add(self, event_type: str, payload: BaseModel) -> None: ...
