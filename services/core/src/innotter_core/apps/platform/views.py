from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse

from innotter_core.apps.platform.auth import get_auth_state_reader

VERSION = "0.2.0"


def live(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def ready(request: HttpRequest) -> JsonResponse:
    checks = {"catalog_mode": settings.INNOTTER_CATALOG_MODE}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    checks["redis"] = "ok" if get_auth_state_reader().ping() else "unavailable"
    ok = checks["database"] == "ok" and checks["redis"] == "ok"
    return JsonResponse({**checks, "version": VERSION}, status=200 if ok else 503)
