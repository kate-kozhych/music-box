from __future__ import annotations

from rest_framework import serializers


class UserSummarySerializer(serializers.Serializer):  # type: ignore[type-arg]
    user_id = serializers.UUIDField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
