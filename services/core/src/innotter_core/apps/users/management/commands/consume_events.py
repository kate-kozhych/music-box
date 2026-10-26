from __future__ import annotations

import signal
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from innotter_core.apps.platform.messaging import Consumer
from innotter_core.apps.users.handlers import HANDLERS, QUEUE


class Command(BaseCommand):
    help = "Consume identity.user.* events and keep the user projection up to date."

    def handle(self, *args: Any, **options: Any) -> None:
        consumer = Consumer(
            settings.INNOTTER_AMQP_URL,
            QUEUE,
            HANDLERS,
            heartbeat_file=settings.INNOTTER_CONSUMER_HEARTBEAT_FILE,
        )
        signal.signal(signal.SIGTERM, lambda *_: consumer.stop())
        signal.signal(signal.SIGINT, lambda *_: consumer.stop())
        consumer.run_forever()
