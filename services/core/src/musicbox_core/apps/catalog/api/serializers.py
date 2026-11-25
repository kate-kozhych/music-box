from __future__ import annotations

from rest_framework import serializers


class AttributionLicenceSerializer(serializers.Serializer):  # type: ignore[type-arg]
    name = serializers.CharField()
    url = serializers.URLField()
    applies_to = serializers.CharField()


class SourceSerializer(serializers.Serializer):  # type: ignore[type-arg]
    name = serializers.CharField()
    url = serializers.URLField()


class CoverArtAttributionSerializer(SourceSerializer):
    text = serializers.CharField()


class AttributionSerializer(serializers.Serializer):  # type: ignore[type-arg]
    source = SourceSerializer()  # type: ignore[assignment]
    text = serializers.CharField()
    licences = AttributionLicenceSerializer(many=True)
    cover_art = CoverArtAttributionSerializer()


class AlbumSearchItemSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField(allow_null=True)
    mbid = serializers.CharField()
    title = serializers.CharField()
    artist_credit = serializers.CharField()
    year = serializers.IntegerField(allow_null=True)
    type = serializers.CharField()
    cover_url = serializers.CharField(allow_null=True)
    imported = serializers.BooleanField()


class AlbumSearchPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = AlbumSearchItemSerializer(many=True)
    page = serializers.IntegerField()
    size = serializers.IntegerField()
    total = serializers.IntegerField()
    external_status = serializers.ChoiceField(choices=["ok", "cached", "unavailable", "offline"])
    attribution = AttributionSerializer()


class ArtistRefSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    mbid = serializers.CharField()
    name = serializers.CharField()
    join_phrase = serializers.CharField(allow_blank=True)
    url = serializers.URLField()


class GenreSerializer(serializers.Serializer):  # type: ignore[type-arg]
    name = serializers.CharField()
    slug = serializers.CharField()


class AlbumStatsSerializer(serializers.Serializer):  # type: ignore[type-arg]
    average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()
    distribution = serializers.ListField(child=serializers.IntegerField())


class ViewerStateSerializer(serializers.Serializer):  # type: ignore[type-arg]
    rating = serializers.FloatField(allow_null=True)
    review_id = serializers.CharField(allow_null=True)
    diary_count = serializers.IntegerField()


class AlbumSummarySerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    mbid = serializers.CharField()
    title = serializers.CharField()
    artist_credit = serializers.CharField()
    year = serializers.IntegerField(allow_null=True)
    type = serializers.CharField()
    cover_url = serializers.CharField(allow_null=True)


class DiscoverAlbumSerializer(AlbumSummarySerializer):
    average = serializers.FloatField(allow_null=True)
    rating_count = serializers.IntegerField()


class DiscoverAlbumPageSerializer(serializers.Serializer):  # type: ignore[type-arg]
    items = DiscoverAlbumSerializer(many=True)
    page = serializers.IntegerField()
    size = serializers.IntegerField()
    total = serializers.IntegerField()
    attribution = AttributionSerializer()


class RecentReviewAuthorSerializer(serializers.Serializer):  # type: ignore[type-arg]
    user_id = serializers.UUIDField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)


class RecentReviewSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    author = RecentReviewAuthorSerializer()
    rating = serializers.FloatField(allow_null=True)
    body = serializers.CharField()
    truncated = serializers.BooleanField()
    like_count = serializers.IntegerField()
    comment_count = serializers.IntegerField()
    liked_by_me = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    edited = serializers.BooleanField()
    hidden = serializers.BooleanField()


class AlbumDetailSerializer(serializers.Serializer):  # type: ignore[type-arg]
    id = serializers.CharField()
    mbid = serializers.CharField()
    title = serializers.CharField()
    artists = ArtistRefSerializer(many=True)
    artist_credit = serializers.CharField()
    type = serializers.CharField()
    secondary_types = serializers.ListField(child=serializers.CharField())
    release_date = serializers.CharField(allow_null=True)
    release_date_precision = serializers.CharField(allow_null=True)
    genres = GenreSerializer(many=True)
    cover_url = serializers.CharField(allow_null=True)
    cover_url_500 = serializers.CharField(allow_null=True)
    stats = AlbumStatsSerializer()
    recent_reviews = RecentReviewSerializer(many=True)
    viewer = ViewerStateSerializer()
    source = SourceSerializer()  # type: ignore[assignment]
    attribution = AttributionSerializer()
