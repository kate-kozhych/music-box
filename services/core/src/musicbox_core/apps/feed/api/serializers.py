from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.catalog.api.serializers import AlbumSummarySerializer
from musicbox_core.apps.users.serializers import UserSummarySerializer


class FeedObjectSerializer(serializers.Serializer):  # type: ignore[type-arg]
    type = serializers.CharField(help_text="post, review, rating or diary_entry")
    id = serializers.CharField()
    excerpt = serializers.CharField(allow_blank=True)
    edited = serializers.BooleanField()


class FeedItemSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    verb = serializers.CharField(help_text="POST, REVIEW, RATING or DIARY")
    created_at = serializers.DateTimeField()
    actor = UserSummarySerializer()
    album = AlbumSummarySerializer(allow_null=True)
    rating = serializers.FloatField(allow_null=True)
    object = FeedObjectSerializer()
    like_count = serializers.IntegerField()
    comment_count = serializers.IntegerField()
    liked_by_me = serializers.BooleanField()


class FeedPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = FeedItemSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
