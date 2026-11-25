from __future__ import annotations

from datetime import datetime
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from musicbox_core.apps.platform.replay import replay, replayable_ids


class Command(BaseCommand):
    help = "Queue published like, comment and content events for another delivery."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--until", required=True, help="only events before this ISO time")
        parser.add_argument("--label", default="replay", help="repeating a label is a no-op")
        parser.add_argument("--apply", action="store_true", help="queue the events")

    def handle(self, *args: Any, **options: Any) -> None:
        ids = replayable_ids(_parse_time(options["until"]))
        if not options["apply"]:
            self.stdout.write(f"{len(ids)} events can be replayed; pass --apply to queue them.")
            return
        self.stdout.write(f"Queued {replay(ids, options['label'])} of {len(ids)} events.")


def _parse_time(raw: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CommandError("--until must be an ISO 8601 time.") from exc
    return parsed if parsed.tzinfo else timezone.make_aware(parsed)
