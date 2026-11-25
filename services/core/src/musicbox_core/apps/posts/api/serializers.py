from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.catalog.api.serializers import AlbumSummarySerializer
from musicbox_core.apps.platform.serializers import PlainTextField, StrictSerializer
from musicbox_core.apps.posts.models import BODY_MAX
from musicbox_core.apps.users.serializers import UserSummarySerializer


class PostCreateSerializer(StrictSerializer):
    body = PlainTextField(max_length=BODY_MAX)
    album_id = serializers.UUIDField(required=False, allow_null=True)


class PostEditSerializer(StrictSerializer):
    body = PlainTextField(required=False, max_length=BODY_MAX)
    album_id = serializers.UUIDField(required=False, allow_null=True)


class PostSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    author = UserSummarySerializer()
    album = AlbumSummarySerializer(allow_null=True)
    body = serializers.CharField()
    like_count = serializers.IntegerField()
    comment_count = serializers.IntegerField()
    liked_by_me = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    edited_at = serializers.DateTimeField(allow_null=True)
    edited = serializers.BooleanField()
    hidden = serializers.BooleanField()
    hidden_reason = serializers.CharField(allow_null=True)


class PostPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = PostSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
