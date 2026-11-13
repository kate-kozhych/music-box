from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "innotter_core.config.settings.base")

app = Celery("innotter_core")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
