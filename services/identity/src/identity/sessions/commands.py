from __future__ import annotations

from dataclasses import dataclass

from innotter_common.auth import Principal


@dataclass(frozen=True, slots=True)
class Login:
    identifier: str
    password: str
    client_ip: str


@dataclass(frozen=True, slots=True)
class Refresh:
    refresh_token: str


@dataclass(frozen=True, slots=True)
class Logout:
    refresh_token: str | None
    principal: Principal | None
