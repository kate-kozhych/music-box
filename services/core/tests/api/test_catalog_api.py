from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from tests.conftest import auth
from tests.fake_redis import FakeRedis

from musicbox_core.apps.catalog import provider as provider_module
from musicbox_core.apps.catalog import search as search_module
from musicbox_core.apps.catalog import tasks as tasks_module
from musicbox_core.apps.catalog.models import Album, DatePrecision, PrimaryType
from musicbox_core.apps.catalog.ports import (
    ArtistRef,
    MetadataUnavailableError,
    ReleaseGroup,
    ReleaseGroupNotFoundError,
    ReleaseGroupSummary,
    UnsupportedReleaseTypeError,
)
from musicbox_core.apps.catalog.services import store_release_group

pytestmark = [pytest.mark.django_db, pytest.mark.api]

MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"
OTHER_MBID = "0e3f1b1c-1d6e-4b0a-9f3a-2c9d4e5f6a7b"
ARTIST_MBID = "87c5dedd-371d-4a2f-bdcb-9ded86cd7d3c"


def stored_album(**overrides: Any) -> Album:
    values: dict[str, Any] = {
        "mbid": MBID,
        "title": "Homogenic",
        "primary_type": PrimaryType.ALBUM,
        "artist_credit": "Björk",
        "secondary_types": ["Live"],
        "first_release_date": dt.date(1997, 9, 22),
        "date_precision": DatePrecision.DAY,
        "artists": (ArtistRef(mbid=ARTIST_MBID, name="Björk", sort_name="Björk"),),
        "genres": (),
    }
    values.update(overrides)
    return store_release_group(ReleaseGroup(**values))


class StubProvider:
    mode = "ok"

    def __init__(
        self,
        *,
        lookup: ReleaseGroup | Exception | None = None,
        results: Sequence[ReleaseGroupSummary] | Exception = (),
    ) -> None:
        self._lookup = lookup
        self._results = results
        self.lookups = 0
        self.searches = 0

    def search_release_groups(
        self, query: str, *, limit: int = 20
    ) -> Sequence[ReleaseGroupSummary]:
        self.searches += 1
        if isinstance(self._results, Exception):
            raise self._results
        return self._results

    def get_release_group(self, mbid: str) -> ReleaseGroup:
        self.lookups += 1
        if isinstance(self._lookup, Exception):
            raise self._lookup
        if self._lookup is None:
            raise ReleaseGroupNotFoundError(mbid)
        return self._lookup

    def browse_artist_release_groups(
        self, artist_mbid: str, *, limit: int = 100
    ) -> Sequence[ReleaseGroupSummary]:
        return ()


Install = Any


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Install:
    entries: dict[str, Any] = {}

    class Cache:
        def get(self, query: str) -> Any:
            return entries.get(query)

        def set(self, query: str, results: Any) -> None:
            entries[query] = list(results)

    def _install(provider: StubProvider) -> StubProvider:
        monkeypatch.setattr(provider_module, "is_live", lambda: True)
        monkeypatch.setattr(search_module, "is_live", lambda: True)
        monkeypatch.setattr("musicbox_core.apps.catalog.api.views.is_live", lambda: True)
        monkeypatch.setattr(search_module, "get_provider", lambda: provider)
        monkeypatch.setattr(search_module, "get_search_cache", lambda: Cache())
        monkeypatch.setattr("musicbox_core.apps.catalog.api.views.get_provider", lambda: provider)
        monkeypatch.setattr(tasks_module, "enqueue_refresh", lambda mbid: False)
        return provider

    return _install


fulltext = pytest.mark.django_db(transaction=True)


@fulltext
def test_offline_search_returns_local_matches(api: APIClient) -> None:
    stored_album()
    response = api.get("/api/v1/albums", {"q": "homogenic"})

    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Homogenic"]
    assert body["external_status"] == "offline"
    assert body["items"][0]["imported"] is True
    assert body["items"][0]["id"] is not None
    assert body["items"][0]["mbid"] == MBID
    assert body["items"][0]["year"] == 1997


@fulltext
def test_search_folds_accents_and_case(api: APIClient) -> None:
    stored_album(title="Homogénic", artist_credit="Björk")
    assert api.get("/api/v1/albums", {"q": "bjork"}).json()["items"]
    assert api.get("/api/v1/albums", {"q": "HOMOGENIC"}).json()["items"]


@fulltext
def test_search_by_artist_name_finds_the_album(api: APIClient) -> None:
    stored_album()
    assert api.get("/api/v1/albums", {"q": "björk"}).json()["items"]


def test_an_empty_result_is_a_200_with_no_items(api: APIClient) -> None:
    response = api.get("/api/v1/albums", {"q": "nothing matches this"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


@pytest.mark.parametrize("query", ["", "a", "x" * 101])
def test_queries_outside_two_to_a_hundred_characters_are_rejected(
    api: APIClient, query: str
) -> None:
    response = api.get("/api/v1/albums", {"q": query})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


@fulltext
def test_the_search_response_carries_attribution(api: APIClient) -> None:
    body = api.get("/api/v1/albums", {"q": "homogenic"}).json()
    assert body["attribution"]["source"]["name"] == "MusicBrainz"
    assert {licence["name"] for licence in body["attribution"]["licences"]} == {
        "CC0 1.0",
        "CC BY-NC-SA 3.0",
    }
    assert "Cover Art Archive" in body["attribution"]["cover_art"]["name"]


@fulltext
def test_live_search_merges_external_hits_and_dedupes_by_mbid(
    api: APIClient, live: Install
) -> None:
    stored_album()
    live(
        StubProvider(
            results=[
                ReleaseGroupSummary(
                    mbid=MBID,
                    title="Homogenic",
                    primary_type=PrimaryType.ALBUM,
                    artist_credit="Björk",
                ),
                ReleaseGroupSummary(
                    mbid=OTHER_MBID,
                    title="Homogenic Live",
                    primary_type=PrimaryType.ALBUM,
                    artist_credit="Björk",
                    first_release_date=dt.date(2001, 1, 1),
                ),
            ]
        )
    )

    body = api.get("/api/v1/albums", {"q": "homogenic"}).json()
    assert body["external_status"] == "ok"
    assert [item["mbid"] for item in body["items"]] == [MBID, OTHER_MBID]
    assert [item["imported"] for item in body["items"]] == [True, False]
    assert body["items"][1]["id"] is None


@fulltext
def test_a_degraded_provider_still_returns_local_results(api: APIClient, live: Install) -> None:
    stored_album()
    live(StubProvider(results=MetadataUnavailableError("down")))

    response = api.get("/api/v1/albums", {"q": "homogenic"})
    assert response.status_code == 200
    body = response.json()
    assert body["external_status"] == "unavailable"
    assert [item["mbid"] for item in body["items"]] == [MBID]


def test_a_repeated_query_is_served_from_the_cache(api: APIClient, live: Install) -> None:
    provider = live(StubProvider(results=[]))

    assert api.get("/api/v1/albums", {"q": "homogenic"}).json()["external_status"] == "ok"
    assert provider.searches == 1
    assert api.get("/api/v1/albums", {"q": "homogenic"}).json()["external_status"] == "cached"
    assert provider.searches == 1


def test_the_album_page_matches_the_documented_shape(api: APIClient) -> None:
    album = stored_album()
    body = api.get(f"/api/v1/albums/{album.id}").json()

    assert body["id"] == str(album.id)
    assert body["mbid"] == MBID
    assert body["title"] == "Homogenic"
    assert body["type"] == "ALBUM"
    assert body["secondary_types"] == ["Live"]
    assert body["release_date"] == "1997-09-22"
    assert body["release_date_precision"] == "DAY"
    assert [a["name"] for a in body["artists"]] == ["Björk"]
    assert body["artists"][0]["url"].endswith(ARTIST_MBID)
    assert body["stats"] == {"average": None, "count": 0, "distribution": [0] * 10}
    assert body["viewer"] == {"rating": None, "review_id": None, "diary_count": 0}
    assert body["recent_reviews"] == []
    assert body["source"] == {
        "name": "MusicBrainz",
        "url": f"https://musicbrainz.org/release-group/{MBID}",
    }


@pytest.mark.parametrize(
    ("precision", "date", "expected"),
    [
        (DatePrecision.YEAR, dt.date(1997, 1, 1), "1997"),
        (DatePrecision.MONTH, dt.date(1997, 9, 1), "1997-09"),
        (DatePrecision.DAY, dt.date(1997, 9, 22), "1997-09-22"),
    ],
)
def test_release_dates_render_at_their_stored_precision(
    api: APIClient, precision: DatePrecision, date: dt.date, expected: str
) -> None:
    album = stored_album(first_release_date=date, date_precision=precision)
    assert api.get(f"/api/v1/albums/{album.id}").json()["release_date"] == expected


def test_an_unknown_album_id_is_404(api: APIClient) -> None:
    response = api.get(f"/api/v1/albums/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_album"


def test_a_malformed_album_id_is_404(api: APIClient) -> None:
    assert api.get("/api/v1/albums/not-a-uuid").status_code == 404


def test_a_guest_sees_the_documented_empty_viewer_state(api: APIClient) -> None:
    album = stored_album()
    assert api.get(f"/api/v1/albums/{album.id}").json()["viewer"]["diary_count"] == 0


def test_an_authenticated_viewer_gets_the_viewer_block(
    api: APIClient, make_user: Any, make_token: Any
) -> None:
    album = stored_album()
    user = make_user("ann")
    body = auth(api, make_token(user)).get(f"/api/v1/albums/{album.id}").json()
    assert set(body["viewer"]) == {"rating", "review_id", "diary_count"}


def test_opening_a_stored_mbid_makes_no_external_call(api: APIClient, live: Install) -> None:
    stored_album()
    provider = live(StubProvider(lookup=ReleaseGroupNotFoundError("should not be called")))

    response = api.get(f"/api/v1/albums/mbid/{MBID}")
    assert response.status_code == 200
    assert provider.lookups == 0


def test_an_unstored_mbid_is_imported_on_demand(api: APIClient, live: Install) -> None:
    provider = live(
        StubProvider(
            lookup=ReleaseGroup(
                mbid=MBID,
                title="Homogenic",
                primary_type=PrimaryType.ALBUM,
                artist_credit="Björk",
                artists=(ArtistRef(mbid=ARTIST_MBID, name="Björk", sort_name="Björk"),),
            )
        )
    )

    response = api.get(f"/api/v1/albums/mbid/{MBID}")
    assert response.status_code == 200
    assert response.json()["title"] == "Homogenic"
    assert provider.lookups == 1
    assert Album.objects.filter(mbid=MBID).exists()
    assert Album.objects.get(mbid=MBID).cover_url_250.endswith(f"{MBID}/front-250")


def test_an_invalid_mbid_is_422(api: APIClient) -> None:
    response = api.get("/api/v1/albums/mbid/not-an-mbid")
    assert response.status_code == 422


def test_a_non_album_release_group_is_422(api: APIClient, live: Install) -> None:
    live(StubProvider(lookup=UnsupportedReleaseTypeError("Single")))
    response = api.get(f"/api/v1/albums/mbid/{MBID}")

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_release_type"


def test_an_unknown_mbid_is_404(api: APIClient, live: Install) -> None:
    live(StubProvider(lookup=ReleaseGroupNotFoundError("gone")))
    response = api.get(f"/api/v1/albums/mbid/{MBID}")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_album"


def test_an_unreachable_provider_is_503_with_retry_after_and_stores_nothing(
    api: APIClient, live: Install
) -> None:
    live(StubProvider(lookup=MetadataUnavailableError("down", retry_after=42)))
    response = api.get(f"/api/v1/albums/mbid/{MBID}")

    assert response.status_code == 503
    assert response.json()["code"] == "catalog_unavailable"
    assert response.headers["Retry-After"] == "42"
    assert not Album.objects.filter(mbid=MBID).exists()


def test_offline_mode_answers_404_for_an_unknown_mbid(api: APIClient) -> None:
    response = api.get(f"/api/v1/albums/mbid/{MBID}")
    assert response.status_code == 404


def test_a_stale_album_is_served_immediately_and_queued_for_refresh(
    api: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    album = stored_album()
    Album.objects.filter(pk=album.pk).update(fetched_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    queued: list[str] = []
    monkeypatch.setattr(tasks_module, "enqueue_refresh", lambda mbid: queued.append(mbid) or True)

    response = api.get(f"/api/v1/albums/{album.id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Homogenic"
    assert queued == [MBID]


def test_a_fresh_album_is_not_queued(api: APIClient, monkeypatch: pytest.MonkeyPatch) -> None:
    album = stored_album()
    queued: list[str] = []
    monkeypatch.setattr(tasks_module, "enqueue_refresh", lambda mbid: queued.append(mbid) or True)

    api.get(f"/api/v1/albums/{album.id}")
    assert queued == []


@override_settings(MUSICBOX_CATALOG_MODE="offline")
def test_offline_mode_never_queues_a_refresh() -> None:
    assert tasks_module.enqueue_refresh(MBID) is False


def test_a_failed_refresh_keeps_the_stored_data(monkeypatch: pytest.MonkeyPatch) -> None:
    album = stored_album()
    monkeypatch.setattr(
        "musicbox_core.apps.catalog.tasks.get_provider",
        lambda: StubProvider(lookup=MetadataUnavailableError("down")),
    )

    assert tasks_module.refresh_album(MBID) is False
    album.refresh_from_db()
    assert album.title == "Homogenic"


def summary(mbid: str, title: str) -> ReleaseGroupSummary:
    return ReleaseGroupSummary(
        mbid=mbid, title=title, primary_type=PrimaryType.ALBUM, artist_credit="Björk"
    )


def mbid_number(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


@fulltext
def test_a_stored_external_hit_outside_the_local_matches_is_shown_as_imported(
    api: APIClient, live: Install
) -> None:
    album = stored_album()
    live(StubProvider(results=[summary(MBID, "Homogenic")]))

    body = api.get("/api/v1/albums", {"q": "unrelated words"}).json()

    assert [(item["id"], item["imported"]) for item in body["items"]] == [(str(album.id), True)]


@fulltext
def test_paging_returns_every_local_and_external_hit_exactly_once(
    api: APIClient, live: Install
) -> None:
    for number in range(5):
        stored_album(mbid=mbid_number(number), title=f"Homogenic {number}")
    live(
        StubProvider(
            results=[summary(mbid_number(number), f"Homogenic {number}") for number in range(9)]
        )
    )

    pages = [
        api.get("/api/v1/albums", {"q": "homogenic", "size": 2, "page": page}).json()
        for page in range(1, 6)
    ]

    found = [item["mbid"] for page in pages for item in page["items"]]
    assert sorted(found) == [mbid_number(number) for number in range(9)]
    assert {page["total"] for page in pages} == {9}
    assert [len(page["items"]) for page in pages] == [2, 2, 2, 2, 1]


def test_live_external_hits_carry_a_thumbnail(api: APIClient, live: Install) -> None:
    live(StubProvider(results=[summary(OTHER_MBID, "Post")]))

    (item,) = api.get("/api/v1/albums", {"q": "post"}).json()["items"]

    assert item["cover_url"].endswith(f"{OTHER_MBID}/front-250")


@fulltext
def test_offline_responses_never_point_at_remote_artwork(api: APIClient) -> None:
    album = stored_album()
    assert album.cover_url_250 is not None

    detail = api.get(f"/api/v1/albums/{album.id}").json()
    (item,) = api.get("/api/v1/albums", {"q": "homogenic"}).json()["items"]

    assert (detail["cover_url"], detail["cover_url_500"]) == (None, None)
    assert item["cover_url"] is None


@fulltext
@pytest.mark.parametrize(
    ("title", "query"),
    [
        ("Asia", "asia"),
        ("Kid A", "kid"),
        ("Hunting High by a-ha", "a-ha"),
        ("Louden Up by !!!", "!!!"),
        ("R&B Gold", "r&b"),
        ("What's Going On", "what's going on"),
        ("さよなら", "さよ"),
    ],
)
def test_titles_with_stopwords_or_punctuation_are_searchable(
    api: APIClient, title: str, query: str
) -> None:
    stored_album(title=title, artist_credit="Zz")

    items = api.get("/api/v1/albums", {"q": query}).json()["items"]

    assert [item["title"] for item in items] == [title]


@fulltext
@pytest.mark.parametrize("query", ['""', "%%", "+-", "()", "** **"])
def test_operator_only_queries_are_literal(api: APIClient, query: str) -> None:
    stored_album()

    response = api.get("/api/v1/albums", {"q": query})

    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.parametrize("mbid", [f" {MBID}", f"{MBID} ", f"{MBID}%0A"])
def test_an_mbid_with_surrounding_whitespace_is_422(api: APIClient, mbid: str) -> None:
    assert api.get(f"/api/v1/albums/mbid/{mbid}").status_code == 422


def test_a_stale_album_is_served_when_the_broker_is_down(
    api: APIClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    album = stored_album()
    Album.objects.filter(pk=album.pk).update(fetched_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    client = FakeRedis()
    monkeypatch.setattr(tasks_module, "is_live", lambda: True)
    monkeypatch.setattr(tasks_module, "cache_redis", lambda: client)

    def unavailable(mbid: str) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks_module.refresh_album, "delay", unavailable)

    assert api.get(f"/api/v1/albums/{album.id}").status_code == 200
    assert client.store == {}


def test_a_second_viewer_does_not_queue_a_second_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[str] = []
    client = FakeRedis()
    monkeypatch.setattr(tasks_module, "is_live", lambda: True)
    monkeypatch.setattr(tasks_module, "cache_redis", lambda: client)
    monkeypatch.setattr(tasks_module.refresh_album, "delay", queued.append)

    assert tasks_module.enqueue_refresh(MBID) is True
    client.advance(599)
    assert tasks_module.enqueue_refresh(MBID) is False
    client.advance(1)
    assert tasks_module.enqueue_refresh(MBID) is True
    assert queued == [MBID, MBID]


def test_an_invalid_mbid_names_the_field(api: APIClient) -> None:
    body = api.get("/api/v1/albums/mbid/not-an-mbid").json()

    assert body["code"] == "validation_error"
    assert [error["field"] for error in body["errors"]] == ["mbid"]


@pytest.mark.parametrize("role", [None, "USER", "ADMIN"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_c04_nobody_can_write_catalog_records(
    api: APIClient, make_user: Any, make_token: Any, method: str, role: str | None
) -> None:
    album = stored_album()
    if role is not None:
        auth(api, make_token(make_user("ann"), role=role))

    response = getattr(api, method)(f"/api/v1/albums/{album.id}", {"title": "x"}, format="json")

    assert response.status_code == 405
    assert response["Content-Type"].startswith("application/problem+json")
    album.refresh_from_db()
    assert album.title == "Homogenic"


@pytest.mark.parametrize("path", ["", f"/mbid/{MBID}"])
def test_c01_to_c03_guests_can_read_the_catalog(api: APIClient, path: str) -> None:
    stored_album()

    assert api.get(f"/api/v1/albums{path}", {"q": "homogenic"}).status_code == 200


@fulltext
def test_us014_offline_endpoints_open_no_outbound_connection(
    api: APIClient, monkeypatch: pytest.MonkeyPatch, settings: Any
) -> None:
    settings.MUSICBOX_CATALOG_MODE = "offline"
    provider_module.get_provider.cache_clear()
    album = stored_album()
    Album.objects.filter(pk=album.pk).update(fetched_at=timezone.now() - dt.timedelta(days=400))

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Offline mode opened an outbound connection.")

    with monkeypatch.context() as guard:
        for target in ("httpx.Client.send", "httpx.Client.__init__"):
            guard.setattr(target, refuse)
        for target in ("socket.create_connection", "socket.getaddrinfo"):
            guard.setattr(target, refuse)

        assert api.get("/api/v1/albums", {"q": "homogenic"}).status_code == 200
        assert api.get("/api/v1/albums", {"q": "absent record"}).json()["items"] == []
        assert api.get(f"/api/v1/albums/{album.id}").status_code == 200
        assert api.get(f"/api/v1/albums/mbid/{album.mbid}").status_code == 200
        assert api.get(f"/api/v1/albums/mbid/{OTHER_MBID}").status_code == 404
