from __future__ import annotations

from rest_framework import serializers

from innotter_core.apps.users.serializers import UserSummarySerializer


class FollowEntrySerializer(serializers.Serializer):  # type: ignore[type-arg]
    user = UserSummarySerializer()
    followed_at = serializers.DateTimeField()
    viewer_follows = serializers.BooleanField(allow_null=True)


class FollowPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = FollowEntrySerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
