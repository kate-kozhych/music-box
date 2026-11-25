from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.catalog.api.serializers import AlbumStatsSerializer, AlbumSummarySerializer
from musicbox_core.apps.platform.serializers import StrictSerializer
from musicbox_core.apps.ratings.values import RatingValueField


class RatingInputSerializer(StrictSerializer):
    value = RatingValueField()


class RatingResultSerializer(serializers.Serializer):  # type: ignore[type-arg]
    value = serializers.FloatField()
    album_stats = AlbumStatsSerializer()


class RatingItemSerializer(serializers.Serializer):  # type: ignore[type-arg]
    album = AlbumSummarySerializer()
    value = serializers.FloatField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class RatingPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = RatingItemSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
