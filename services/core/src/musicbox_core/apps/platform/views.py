from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse

from musicbox_core.apps.platform.auth import get_auth_state_reader

log = logging.getLogger(__name__)

VERSION = "0.2.0"

_PROBES: dict[str, Callable[[], str]] = {}


def register_probe(name: str, probe: Callable[[], str]) -> None:
    _PROBES[name] = probe


def live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def ready(request: HttpRequest) -> JsonResponse:
    checks: dict[str, str] = {"catalog_mode": settings.MUSICBOX_CATALOG_MODE}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    checks["redis"] = "ok" if get_auth_state_reader().ping() else "unavailable"
    ok = checks["database"] == "ok" and checks["redis"] == "ok"
    for name, probe in _PROBES.items():
        try:
            checks[name] = probe()
        except Exception:
            log.warning("Readiness probe failed", extra={"probe": name}, exc_info=True)
            checks[name] = "unknown"
    return JsonResponse({**checks, "version": VERSION}, status=200 if ok else 503)
