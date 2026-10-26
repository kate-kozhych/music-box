import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from identity.accounts.http import router as account_router
from identity.administration.http import router as admin_router
from identity.bootstrap import Container, build_container
from identity.kernel.outbox import OutboxRelay
from identity.sessions.http import internal_router as jwks_router
from identity.sessions.http import router as auth_router
from identity.settings import Settings
from innotter_common.adapters.fastapi import install
from innotter_common.logging import configure

log = logging.getLogger(__name__)

VERSION = "0.2.0"


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # values come from the env
    configure("identity", settings.log_level)
    container = container or build_container(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        relay: OutboxRelay | None = None
        task: asyncio.Task[None] | None = None
        if settings.outbox_relay_enabled:
            relay = OutboxRelay(
                container.session_factory,
                settings.amqp_url.get_secret_value(),
                poll_interval=settings.outbox_poll_interval_seconds,
            )
            task = asyncio.create_task(relay.run(), name="outbox-relay")
        log.info("Identity service started", extra={"version": VERSION})
        try:
            yield
        finally:
            if relay and task:
                relay.stop()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await container.aclose()

    app = FastAPI(
        title="Innotter Identity API",
        version=VERSION,
        description="Accounts, sessions (EdDSA JWT + rotating refresh cookie) and user administration.",
        docs_url="/api/v1/auth/docs",
        openapi_url="/api/v1/auth/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.container = container
    install(app)
    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(admin_router)
    app.include_router(jwks_router)

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request) -> Any:
        checks: dict[str, str] = {}
        try:
            async with container.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "unavailable"
        try:
            await container.redis_auth.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({**checks, "version": VERSION}, status_code=200 if ok else 503)

    return app
