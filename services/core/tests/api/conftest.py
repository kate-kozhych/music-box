from __future__ import annotations

from collections.abc import Callable

import pytest
from rest_framework.test import APIClient
from tests.conftest import ProfileFactory, TokenFactory, auth

from musicbox_core.apps.catalog.models import Album, AlbumRatingStats, PrimaryType
from musicbox_core.apps.users.models import UserProfile

AlbumFactory = Callable[..., Album]
ClientFactory = Callable[..., APIClient]


@pytest.fixture
def make_album(db: None) -> AlbumFactory:
    def _make(number: int = 1) -> Album:
        album = Album.objects.create(
            mbid=f"00000000-0000-4000-8000-{number:012d}",
            title=f"Album {number}",
            primary_type=PrimaryType.ALBUM,
            artist_credit="Artist",
        )
        AlbumRatingStats.objects.create(album=album)
        return album

    return _make


@pytest.fixture
def client_for(make_token: TokenFactory) -> ClientFactory:
    def _client(user: UserProfile | None = None, role: str = "USER") -> APIClient:
        client = APIClient()
        return client if user is None else auth(client, make_token(user, role=role))

    return _client


@pytest.fixture
def ann(make_user: ProfileFactory) -> UserProfile:
    return make_user("ann")


@pytest.fixture
def ben(make_user: ProfileFactory) -> UserProfile:
    return make_user("ben")


@pytest.fixture
def root(make_user: ProfileFactory) -> UserProfile:
    return make_user("root")
