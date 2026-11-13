from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from innotter_common.auth import AsyncAuthStateReader, Principal, TokenVerifier
from innotter_common.errors import unauthorized
from innotter_common.logging import user_id_var

BEARER = "bearer"


@dataclass(frozen=True, slots=True)
class Authenticator:
    verifier: TokenVerifier
    state: AsyncAuthStateReader

    async def principal(self, request: Request) -> Principal:
        header = request.headers.get("authorization", "").split()
        if len(header) != 2 or header[0].lower() != BEARER:
            raise unauthorized(detail="Malformed Authorization header.")
        principal = self.verifier.verify(header[1])
        await self.state.ensure_active(principal, fail_open=request.method in {"GET", "HEAD"})
        user_id_var.set(str(principal.user_id))
        return principal


async def current_principal(request: Request) -> Principal:
    authenticator: Authenticator = request.app.state.authenticator
    return await authenticator.principal(request)
