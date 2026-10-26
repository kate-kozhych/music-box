from __future__ import annotations

from django.db import models

from innotter_core.apps.platform.fields import BinaryUUIDField


class OutboxEvent(models.Model):
    event_id = BinaryUUIDField(primary_key=True)
    type = models.CharField(max_length=100)
    payload = models.JSONField()
    correlation_id = models.CharField(max_length=128, null=True, blank=True)
    occurred_at = models.DateTimeField()
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "outbox"
        indexes = [models.Index(fields=["published_at"], name="outbox_published_at_idx")]


class InboxEvent(models.Model):
    event_id = BinaryUUIDField(primary_key=True)
    type = models.CharField(max_length=100)
    processed_at = models.DateTimeField()

    class Meta:
        db_table = "inbox"
