from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.interactions.models import BODY_MAX
from musicbox_core.apps.platform.serializers import PlainTextField, StrictSerializer
from musicbox_core.apps.users.serializers import UserSummarySerializer


class CommentInputSerializer(StrictSerializer):
    body = PlainTextField(max_length=BODY_MAX)


class CommentSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    target_type = serializers.CharField(help_text="post or review")
    target_id = serializers.CharField()
    author = UserSummarySerializer(allow_null=True)
    body = serializers.CharField()
    deleted = serializers.BooleanField()
    like_count = serializers.IntegerField()
    liked_by_me = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    edited_at = serializers.DateTimeField(allow_null=True)
    edited = serializers.BooleanField()
    hidden = serializers.BooleanField()
    hidden_reason = serializers.CharField(allow_null=True)


class CommentPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = CommentSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
