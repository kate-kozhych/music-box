"""Settings used only by `mypy` (django-stubs plugin), never at runtime.

The real settings read a fail-fast `CoreEnv` (NFR-027), so importing them needs
a populated environment. Type checking must not depend on one — and must not
be able to reach a database either — so this module injects placeholder values
before importing `base`.
"""

from __future__ import annotations

import os

_PLACEHOLDERS = {
    "CORE_SECRET_KEY": "typecheck-only-placeholder-" + "x" * 50,
    "CORE_DB_HOST": "localhost",
    "CORE_DB_NAME": "typecheck",
    "CORE_DB_USER": "typecheck",
    "CORE_DB_PASSWORD": "typecheck",
    "CORE_REDIS_AUTH_URL": "redis://localhost:6379/0",
    "CORE_AMQP_URL": "amqp://localhost:5672/",
}
for _name, _value in _PLACEHOLDERS.items():
    os.environ.setdefault(_name, _value)

from musicbox_core.config.settings.base import *  # noqa: E402, F403
