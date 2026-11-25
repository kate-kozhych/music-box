from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.catalog.api.serializers import AlbumSummarySerializer
from musicbox_core.apps.platform.serializers import PlainTextField, StrictSerializer
from musicbox_core.apps.ratings.values import RatingValueField
from musicbox_core.apps.reviews.models import BODY_MAX
from musicbox_core.apps.users.serializers import UserSummarySerializer


class ReviewCreateSerializer(StrictSerializer):
    body = PlainTextField(max_length=BODY_MAX)
    rating = RatingValueField(required=False, allow_null=True)


class ReviewEditSerializer(StrictSerializer):
    body = PlainTextField(max_length=BODY_MAX)


class ReviewSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    album = AlbumSummarySerializer()
    author = UserSummarySerializer()
    rating = serializers.FloatField(allow_null=True)
    body = serializers.CharField()
    truncated = serializers.BooleanField()
    like_count = serializers.IntegerField()
    comment_count = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    edited_at = serializers.DateTimeField(allow_null=True)
    edited = serializers.BooleanField()
    hidden = serializers.BooleanField()
    hidden_reason = serializers.CharField(allow_null=True)


class ReviewPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = ReviewSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
