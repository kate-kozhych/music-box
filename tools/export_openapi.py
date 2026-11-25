#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "contracts" / "openapi"
EVENTS_DIR = ROOT / "contracts" / "events"

COMMON_ENV = {
    "SECRET": "x" * 60,
    "HOST": "localhost",
}

IDENTITY_ENV = {
    "IDENTITY_ENV": "test",
    "IDENTITY_DB_HOST": "localhost",
    "IDENTITY_DB_NAME": "identity",
    "IDENTITY_DB_USER": "identity",
    "IDENTITY_DB_PASSWORD": "placeholder",
    "IDENTITY_REDIS_AUTH_URL": "redis://localhost:6379/0",
    "IDENTITY_REDIS_THROTTLE_URL": "redis://localhost:6379/3",
    "IDENTITY_AMQP_URL": "amqp://localhost",
    "IDENTITY_KEYS_DIR": "/tmp/musicbox-keys",  # noqa: S108
    "IDENTITY_AVATAR_DIR": "/tmp/musicbox-avatars",  # noqa: S108
}

CORE_ENV = {
    "CORE_ENV": "test",
    "CORE_SECRET_KEY": COMMON_ENV["SECRET"],
    "CORE_DB_HOST": "localhost",
    "CORE_DB_NAME": "core",
    "CORE_DB_USER": "core",
    "CORE_DB_PASSWORD": "placeholder",
    "CORE_REDIS_AUTH_URL": "redis://localhost:6379/0",
    "CORE_AMQP_URL": "amqp://localhost",
    "CORE_CATALOG_MODE": "offline",
    "DJANGO_SETTINGS_MODULE": "musicbox_core.config.settings.test",
}

NOTIFICATION_ENV = {
    "NOTIF_ENV": "test",
    "NOTIF_MONGO_URL": "mongodb://localhost:27017",
    "NOTIF_MONGO_DB": "notification",
    "NOTIF_REDIS_AUTH_URL": "redis://localhost:6379/0",
    "NOTIF_AMQP_URL": "amqp://localhost",
}

FASTAPI_SNIPPET = """
import json, sys
from {module} import create_app
sys.stdout.write(json.dumps(create_app().openapi()))
"""

DJANGO_SNIPPET = """
import json, sys, django
django.setup()
from drf_spectacular.generators import SchemaGenerator
sys.stdout.write(json.dumps(SchemaGenerator().get_schema(request=None, public=True)))
"""

EVENTS_SNIPPET = """
import json, sys
from musicbox_common.events import contract_schemas
sys.stdout.write(json.dumps(contract_schemas()))
"""

SERVICES = [
    ("identity", "services/identity", IDENTITY_ENV, FASTAPI_SNIPPET.format(module="identity.app")),
    ("core", "services/core", CORE_ENV, DJANGO_SNIPPET),
    (
        "notification",
        "services/notification",
        NOTIFICATION_ENV,
        FASTAPI_SNIPPET.format(module="notifier.app"),
    ),
]


def export(name: str, workdir: str, env: dict[str, str], snippet: str) -> str:
    import os

    result = subprocess.run(  # noqa: S603
        ["poetry", "run", "python", "-c", snippet],  # noqa: S607
        cwd=ROOT / workdir,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"{name}: export failed\n{result.stderr}")
    return result.stdout


def render(document: object) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def documents() -> dict[Path, str]:
    contents = {
        OUT_DIR / f"{name}.json": render(json.loads(export(name, workdir, env, snippet)))
        for name, workdir, env, snippet in SERVICES
    }
    events = json.loads(export("events", "services/identity", IDENTITY_ENV, EVENTS_SNIPPET))
    return contents | {
        EVENTS_DIR / f"{name}.json": render(schema) for name, schema in events.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Fail if a file is stale.")
    args = parser.parse_args()

    contents = documents()
    known = {*OUT_DIR.glob("*.json"), *EVENTS_DIR.glob("*.json")}
    stale = sorted(
        str(path.relative_to(ROOT))
        for path in known | set(contents)
        if not path.exists() or path.read_text(encoding="utf-8") != contents.get(path)
    )
    if not args.check:
        for path in known - set(contents):
            path.unlink()
        for path, content in contents.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
        return 0

    if stale:
        print(f"Stale contracts: {', '.join(stale)}. Run tools/export_openapi.py.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
