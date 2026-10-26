from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient

from innotter_common.adapters.fastapi import install
from innotter_common.logging import configure
from notifier.settings import Settings

VERSION = "0.2.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure("notification", settings.log_level)
    mongo: AsyncMongoClient[Any] = AsyncMongoClient(
        settings.mongo_url.get_secret_value(), serverSelectionTimeoutMS=2000
    )
    redis_auth = aioredis.from_url(settings.redis_auth_url, socket_timeout=2)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await mongo.close()
            await redis_auth.aclose()

    app = FastAPI(
        title="Innotter Notification API",
        version=VERSION,
        docs_url="/api/v1/notifications/docs",
        openapi_url="/api/v1/notifications/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    install(app)

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            await mongo.admin.command("ping")
            checks["mongo"] = "ok"
        except Exception:
            checks["mongo"] = "unavailable"
        try:
            await redis_auth.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
        checks["consumer"] = "not_implemented"
        ok = checks["mongo"] == "ok" and checks["redis"] == "ok"
        return JSONResponse({**checks, "version": VERSION}, status_code=200 if ok else 503)

    return app
