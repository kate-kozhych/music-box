from __future__ import annotations

from rest_framework import serializers


class CountsSerializer(serializers.Serializer):  # type: ignore[type-arg]
    ratings = serializers.IntegerField()
    reviews = serializers.IntegerField()
    diary_entries = serializers.IntegerField()
    posts = serializers.IntegerField()
    followers = serializers.IntegerField()
    following = serializers.IntegerField()


class FavouritesSerializer(serializers.Serializer):  # type: ignore[type-arg]
    albums = serializers.ListField(child=serializers.DictField())
    artists = serializers.ListField(child=serializers.DictField())


class ViewerSerializer(serializers.Serializer):  # type: ignore[type-arg]
    is_self = serializers.BooleanField()
    follows = serializers.BooleanField(allow_null=True)


class PublicProfileSerializer(serializers.Serializer):  # type: ignore[type-arg]
    user_id = serializers.UUIDField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    bio = serializers.CharField(allow_blank=True)
    avatar_url = serializers.CharField(allow_null=True)
    status = serializers.CharField(required=False, help_text="Only shown to Admins (P02).")
    joined_at = serializers.DateTimeField()
    counts = CountsSerializer()
    favourites = FavouritesSerializer()
    viewer = ViewerSerializer()
