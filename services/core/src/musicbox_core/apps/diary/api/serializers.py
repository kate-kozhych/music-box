from __future__ import annotations

from rest_framework import serializers

from musicbox_core.apps.catalog.api.serializers import AlbumSummarySerializer
from musicbox_core.apps.diary.models import NOTE_MAX, Visibility
from musicbox_core.apps.platform.serializers import (
    CalendarDateField,
    PlainTextField,
    StrictSerializer,
)
from musicbox_core.apps.ratings.values import RatingValueField
from musicbox_core.apps.users.serializers import UserSummarySerializer


class DiaryEditSerializer(StrictSerializer):
    listened_on = CalendarDateField(required=False)
    rating = RatingValueField(required=False, allow_null=True)
    note = PlainTextField(required=False, allow_blank=True, max_length=NOTE_MAX)
    visibility = serializers.ChoiceField(required=False, choices=Visibility.choices)


class DiaryCreateSerializer(DiaryEditSerializer):
    album_id = serializers.UUIDField()


class DiaryEntrySerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    user = UserSummarySerializer()
    album = AlbumSummarySerializer()
    listened_on = serializers.DateField()
    rating = serializers.FloatField(allow_null=True)
    note = serializers.CharField(allow_blank=True)
    truncated = serializers.BooleanField()
    relisten = serializers.BooleanField()
    visibility = serializers.ChoiceField(choices=Visibility.choices)
    hidden = serializers.BooleanField()
    hidden_reason = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class DiaryPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = DiaryEntrySerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)
