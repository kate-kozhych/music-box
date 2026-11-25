from __future__ import annotations

from typing import ClassVar

from django.db import models
from django.db.models import Q

from musicbox_common.ids import uuid7
from musicbox_core.apps.platform.content import ContentType
from musicbox_core.apps.platform.fields import BinaryUUIDField

REASON_MAX = 500


class Action(models.TextChoices):
    HIDE = "hide"
    RESTORE = "restore"


class ModerationAction(models.Model):
    id = BinaryUUIDField(primary_key=True, default=uuid7, editable=False)
    admin_id = BinaryUUIDField()
    target_type = models.CharField(max_length=20)
    target_id = BinaryUUIDField()
    action = models.CharField(max_length=10, choices=Action.choices)
    reason = models.CharField(max_length=REASON_MAX, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "moderation_action"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=Q(target_type__in=[content.value for content in ContentType]),
                name="moderation_target_type_known",
            ),
            models.CheckConstraint(
                condition=Q(action=Action.HIDE, reason__isnull=False)
                | Q(action=Action.RESTORE, reason__isnull=True),
                name="moderation_hide_has_reason",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["target_type", "target_id"], name="moderation_target_idx"),
            models.Index(fields=["created_at", "id"], name="moderation_created_idx"),
        ]
