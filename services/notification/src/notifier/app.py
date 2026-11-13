from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pymongo import AsyncMongoClient

from innotter_common.adapters.fastapi import install
from innotter_common.auth import AsyncAuthStateReader, JwksClient, TokenVerifier
from innotter_common.logging import configure
from notifier import api
from notifier.auth import Authenticator
from notifier.db import ensure_indexes
from notifier.inbox import EventConsumer
from notifier.settings import Settings

log = logging.getLogger(__name__)

VERSION = "0.2.0"
SHUTDOWN_TIMEOUT_S = 10.0


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]
    configure("notification", settings.log_level)
    mongo: AsyncMongoClient[Any] = AsyncMongoClient(
        settings.mongo_url.get_secret_value(), serverSelectionTimeoutMS=2000
    )
    database = mongo[settings.mongo_db]
    redis_auth = aioredis.from_url(settings.redis_auth_url, socket_timeout=2)
    consumer = EventConsumer(
        settings.amqp_url.get_secret_value(),
        database,
        prefetch=settings.consumer_prefetch,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await ensure_indexes(database, processed_event_ttl_days=settings.processed_event_ttl_days)
        task: asyncio.Task[None] | None = None
        if settings.consumers_enabled:
            task = asyncio.create_task(consumer.run(), name="notification-consumers")
        try:
            yield
        finally:
            if task is not None:
                consumer.stop()
                try:
                    await asyncio.wait_for(task, timeout=SHUTDOWN_TIMEOUT_S)
                except TimeoutError:
                    log.warning("Consumers did not stop in time; cancelling")
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
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
    app.state.database = database
    app.state.consumer = consumer
    app.state.authenticator = Authenticator(
        verifier=TokenVerifier(
            JwksClient(settings.jwks_url),
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        ),
        state=AsyncAuthStateReader(redis_auth),
    )
    app.include_router(api.router)

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
        checks["consumer"] = (
            "disabled" if not settings.consumers_enabled else app.state.consumer.state
        )
        consumer_ok = checks["consumer"] in {"running", "disabled"}
        ok = checks["mongo"] == "ok" and checks["redis"] == "ok" and consumer_ok
        return JSONResponse({**checks, "version": VERSION}, status_code=200 if ok else 503)

    return app
