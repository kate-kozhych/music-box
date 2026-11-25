from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.moderation.models import REASON_MAX
from musicbox_core.apps.platform.serializers import PlainTextField, StrictSerializer
from musicbox_core.apps.users.serializers import UserSummarySerializer


class HideSerializer(StrictSerializer):
    reason = PlainTextField(max_length=REASON_MAX)


class ModerationActionSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    admin_id = serializers.UUIDField()
    admin = UserSummarySerializer(allow_null=True)
    target_type = serializers.CharField(help_text="post, review, comment or diary_entry")
    target_id = serializers.CharField()
    action = serializers.CharField(help_text="hide or restore")
    reason = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class ModerationLogSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = ModerationActionSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
