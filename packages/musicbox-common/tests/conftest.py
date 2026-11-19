"""Minimal Django setup so `adapters/django.py` can be tested on its own.

The shared library has no models, settings or apps of its own; it only needs
Django's request/response machinery to exist (NFR-072). This runs at import
time because DRF exception classes resolve translations as soon as they are
constructed — including at test-collection time.
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="musicbox-common-tests",
        ALLOWED_HOSTS=["testserver"],
        INSTALLED_APPS=["django.contrib.contenttypes", "rest_framework"],
        DATABASES={},
        USE_I18N=False,
        USE_TZ=True,
    )
    django.setup()
