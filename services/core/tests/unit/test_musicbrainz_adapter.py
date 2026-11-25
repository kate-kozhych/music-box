from __future__ import annotations

from typing import Any

import httpx
import pytest
import redis as redis_lib
import respx
from tests.fake_redis import FakeRedis

from musicbox_core.apps.catalog.models import DatePrecision, PrimaryType
from musicbox_core.apps.catalog.musicbrainz import (
    BASE_URL,
    MusicBrainzProvider,
    build_user_agent,
)
from musicbox_core.apps.catalog.ports import (
    MetadataUnavailableError,
    ReleaseGroupNotFoundError,
    UnsupportedReleaseTypeError,
)
from musicbox_core.apps.catalog.throttle import CircuitBreaker, RedisRateLimiter

RG_MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"

RELEASE_GROUP: dict[str, Any] = {
    "id": RG_MBID,
    "title": "Homogenic",
    "primary-type": "Album",
    "secondary-types": [],
    "first-release-date": "1997-09-22",
    "artist-credit": [
        {
            "name": "Björk",
            "joinphrase": "",
            "artist": {
                "id": "87c5dedd-371d-4a2f-bdcb-9ded86cd7d3c",
                "name": "Björk",
                "sort-name": "Björk",
                "type": "Person",
                "country": "IS",
            },
        }
    ],
    "genres": [
        {"id": "aaaaaaaa-0000-4000-8000-000000000001", "name": "art pop", "count": 12},
        {"id": "aaaaaaaa-0000-4000-8000-000000000002", "name": "trip hop", "count": 30},
    ],
}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make_provider(clock: Clock, **kwargs: Any) -> MusicBrainzProvider:
    return MusicBrainzProvider(
        user_agent="Musicbox/0.2.0 ( test@musicbox.local )",
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **kwargs,
    )


def test_user_agent_carries_name_version_and_contact() -> None:
    assert build_user_agent("0.2.0", "dev@example.com") == "Musicbox/0.2.0 ( dev@example.com )"


def test_user_agent_requires_a_contact() -> None:
    with pytest.raises(ValueError, match="CORE_MB_USER_AGENT_CONTACT"):
        build_user_agent("0.2.0", "   ")


@respx.mock
def test_every_request_sends_the_user_agent(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json=RELEASE_GROUP)
    )
    make_provider(clock).get_release_group(RG_MBID)
    assert route.calls.last.request.headers["User-Agent"] == (
        "Musicbox/0.2.0 ( test@musicbox.local )"
    )


@respx.mock
def test_lookup_maps_the_release_group(clock: Clock) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json=RELEASE_GROUP)
    )
    album = make_provider(clock).get_release_group(RG_MBID)

    assert album.mbid == RG_MBID
    assert album.title == "Homogenic"
    assert album.primary_type == PrimaryType.ALBUM
    assert album.artist_credit == "Björk"
    assert album.date_precision == DatePrecision.DAY
    assert [a.name for a in album.artists] == ["Björk"]
    assert [g.name for g in album.genres] == ["trip hop", "art pop"]


@respx.mock
def test_search_filters_to_albums_and_eps(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group").mock(
        return_value=httpx.Response(
            200,
            json={
                "release-groups": [
                    RELEASE_GROUP,
                    {
                        **RELEASE_GROUP,
                        "id": "0" * 8 + "-0000-4000-8000-000000000009",
                        "primary-type": "Single",
                    },
                ]
            },
        )
    )
    results = make_provider(clock).search_release_groups("homogenic")

    assert [r.mbid for r in results] == [RG_MBID]
    query = route.calls.last.request.url.params["query"]
    assert "primarytype:album" in query


@respx.mock
def test_search_escapes_lucene_syntax(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group").mock(
        return_value=httpx.Response(200, json={"release-groups": []})
    )
    make_provider(clock).search_release_groups('title:"x" OR (y)')
    query = route.calls.last.request.url.params["query"]
    assert '\\"' in query
    assert "\\(" in query


@respx.mock
def test_browse_uses_the_documented_type_and_limit(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group").mock(
        return_value=httpx.Response(200, json={"release-groups": [RELEASE_GROUP]})
    )
    make_provider(clock).browse_artist_release_groups("87c5dedd-371d-4a2f-bdcb-9ded86cd7d3c")
    params = route.calls.last.request.url.params
    assert params["type"] == "album|ep"
    assert params["limit"] == "100"


@respx.mock
def test_only_catalog_terms_are_sent(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group").mock(
        return_value=httpx.Response(200, json={"release-groups": []})
    )
    make_provider(clock).search_release_groups("blue train")

    request = route.calls.last.request
    assert set(request.url.params) <= {"query", "limit", "offset", "fmt"}
    assert "Cookie" not in request.headers
    assert "Authorization" not in request.headers


@respx.mock
def test_404_is_not_found(clock: Clock) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=httpx.Response(404))
    with pytest.raises(ReleaseGroupNotFoundError):
        make_provider(clock).get_release_group(RG_MBID)


def test_an_invalid_mbid_never_reaches_the_network(clock: Clock) -> None:
    with pytest.raises(ReleaseGroupNotFoundError):
        make_provider(clock).get_release_group("not-an-mbid")


@respx.mock
def test_a_single_is_rejected(clock: Clock) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json={**RELEASE_GROUP, "primary-type": "Single"})
    )
    with pytest.raises(UnsupportedReleaseTypeError):
        make_provider(clock).get_release_group(RG_MBID)


@respx.mock
def test_503_is_retried_twice_then_degrades(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=httpx.Response(503))
    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)

    assert route.call_count == 3
    assert len(clock.slept) == 2
    assert 0.8 <= clock.slept[0] <= 1.2
    assert 1.6 <= clock.slept[1] <= 2.4


@respx.mock
def test_a_retry_succeeds(clock: Clock) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=RELEASE_GROUP)]
    )
    assert make_provider(clock).get_release_group(RG_MBID).title == "Homogenic"


@respx.mock
def test_retry_after_is_honoured(clock: Clock) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=RELEASE_GROUP),
        ]
    )
    make_provider(clock).get_release_group(RG_MBID)
    assert clock.slept == [3.0]


@respx.mock
def test_timeouts_are_retried(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        side_effect=httpx.ReadTimeout("too slow")
    )
    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)
    assert route.call_count == 3


@respx.mock
def test_other_4xx_is_not_retried(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=httpx.Response(403))
    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)
    assert route.call_count == 1


@respx.mock
def test_the_total_budget_is_bounded(clock: Clock) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(503, headers={"Retry-After": "7"})
    )
    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)
    assert clock.monotonic() < 8.0


@respx.mock
def test_each_call_takes_a_shared_slot(clock: Clock) -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, sleep=redis.advance, monotonic=lambda: redis.now)
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json=RELEASE_GROUP)
    )
    provider = make_provider(clock, limiter=limiter)

    provider.get_release_group(RG_MBID)
    assert redis.get("mb:slot") is not None
    provider.get_release_group(RG_MBID)
    assert redis.now >= 1.0


def test_a_caller_that_cannot_get_a_slot_degrades(clock: Clock) -> None:
    redis = FakeRedis()
    redis.set("mb:slot", b"1", px=60_000)
    limiter = RedisRateLimiter(redis, sleep=redis.advance, monotonic=lambda: redis.now)
    provider = make_provider(clock, limiter=limiter, acquire_timeout=3.0)

    with pytest.raises(MetadataUnavailableError, match="slot"):
        provider.get_release_group(RG_MBID)
    assert 3.0 <= redis.now < 4.0


@respx.mock
def test_the_circuit_opens_after_five_failures(clock: Clock) -> None:
    redis = FakeRedis()
    breaker = CircuitBreaker(redis)
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=httpx.Response(503))
    provider = make_provider(clock, breaker=breaker)

    for _ in range(2):
        with pytest.raises(MetadataUnavailableError):
            provider.get_release_group(RG_MBID)

    assert breaker.is_open()
    calls_before = route.call_count
    with pytest.raises(MetadataUnavailableError, match="circuit breaker"):
        provider.get_release_group(RG_MBID)
    assert route.call_count == calls_before


@respx.mock
def test_a_success_closes_the_circuit(clock: Clock) -> None:
    redis = FakeRedis()
    breaker = CircuitBreaker(redis)
    breaker.record_failure()
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json=RELEASE_GROUP)
    )
    make_provider(clock, breaker=breaker).get_release_group(RG_MBID)
    assert redis.get("mb:cb:failures") is None


@respx.mock
def test_slow_attempts_stay_inside_the_total_budget(clock: Clock) -> None:
    def time_out(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions["timeout"]
        clock.now += timeout["connect"] + timeout["read"]
        raise httpx.ReadTimeout("too slow")

    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(side_effect=time_out)

    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)

    assert clock.monotonic() <= 8.0


@respx.mock
def test_waiting_for_a_slot_counts_against_the_total_budget(clock: Clock) -> None:
    class SlowLimiter:
        def acquire(self, timeout_s: float = 3.0) -> bool:
            clock.now += timeout_s
            return True

    def time_out(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions["timeout"]
        clock.now += timeout["connect"] + timeout["read"]
        raise httpx.ReadTimeout("too slow")

    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(side_effect=time_out)

    with pytest.raises(MetadataUnavailableError):
        make_provider(clock, limiter=SlowLimiter()).get_release_group(RG_MBID)

    assert clock.monotonic() <= 8.0


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"primary-type": "Album"}),
        httpx.Response(301, headers={"Location": "https://example.org"}),
    ],
)
def test_unusable_answers_degrade(clock: Clock, response: httpx.Response) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=response)

    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)


def test_an_unreachable_limiter_store_degrades(clock: Clock) -> None:
    class BrokenRedis(FakeRedis):
        def exists(self, *names: str) -> int:
            raise redis_lib.ConnectionError("down")

    provider = make_provider(clock, breaker=CircuitBreaker(BrokenRedis()))

    with pytest.raises(MetadataUnavailableError):
        provider.get_release_group(RG_MBID)


@respx.mock
def test_no_call_is_made_after_the_fifth_consecutive_failure(clock: Clock) -> None:
    breaker = CircuitBreaker(FakeRedis())
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=httpx.Response(503))
    provider = make_provider(clock, breaker=breaker)

    for _ in range(3):
        with pytest.raises(MetadataUnavailableError):
            provider.get_release_group(RG_MBID)

    assert route.call_count == 5


@respx.mock
def test_a_failed_trial_call_reopens_the_circuit_without_retrying(clock: Clock) -> None:
    store = FakeRedis()
    breaker = CircuitBreaker(store)
    for _ in range(5):
        breaker.record_failure()
    store.advance(61)
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(return_value=httpx.Response(503))

    with pytest.raises(MetadataUnavailableError):
        make_provider(clock, breaker=breaker).get_release_group(RG_MBID)

    assert route.call_count == 1
    assert breaker.is_open()


@respx.mock
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "Blue Train",
            "(releasegroup:(blue) OR artist:(blue)) AND (releasegroup:(train) OR artist:(train))",
        ),
        (
            "this AND",
            "(releasegroup:(this) OR artist:(this)) AND (releasegroup:(and) OR artist:(and))",
        ),
        (
            "Simon & Garfunkel",
            "(releasegroup:(simon) OR artist:(simon)) AND (releasegroup:(garfunkel) OR artist:(garfunkel))",
        ),
        ("a:b", "(releasegroup:(a\\:b) OR artist:(a\\:b))"),
        ("!!!", "(releasegroup:(\\!\\!\\!) OR artist:(\\!\\!\\!))"),
    ],
)
def test_search_matches_titles_and_artists_without_lucene_operators(
    clock: Clock, query: str, expected: str
) -> None:
    route = respx.get(f"{BASE_URL}release-group").mock(
        return_value=httpx.Response(200, json={"release-groups": []})
    )

    make_provider(clock).search_release_groups(query)

    sent = route.calls.last.request.url.params["query"]
    assert sent == f"{expected} AND (primarytype:album OR primarytype:ep)"


@respx.mock
def test_includes_are_sent_with_the_documented_separator(clock: Clock) -> None:
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json=RELEASE_GROUP)
    )

    make_provider(clock).get_release_group(RG_MBID)

    query = route.calls.last.request.url.query
    assert b"%2B" not in query
    assert route.calls.last.request.url.params["inc"].split() == ["artist-credits", "genres"]


@respx.mock
@pytest.mark.parametrize("status", [500, 502, 504])
def test_other_server_errors_are_not_retried_but_count_as_failures(
    clock: Clock, status: int
) -> None:
    store = FakeRedis()
    route = respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(status)
    )

    with pytest.raises(MetadataUnavailableError):
        make_provider(clock, breaker=CircuitBreaker(store)).get_release_group(RG_MBID)

    assert route.call_count == 1
    assert store.get("mb:cb:failures") == b"1"


@respx.mock
@pytest.mark.parametrize(
    ("header", "expected"), [("42", 42), ("7.2", 8), ("inf", 30), ("nan", 30), ("soon", 30)]
)
def test_the_upstream_retry_after_is_passed_on(clock: Clock, header: str, expected: int) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(503, headers={"Retry-After": header})
    )

    with pytest.raises(MetadataUnavailableError) as raised:
        make_provider(clock).get_release_group(RG_MBID)

    assert raised.value.retry_after == expected


@respx.mock
@pytest.mark.parametrize(
    "malformed",
    [
        {"title": ""},
        {"artist-credit": ["x"]},
        {"genres": [{"id": "a", "name": "b", "count": "many"}]},
    ],
)
def test_malformed_release_groups_degrade(clock: Clock, malformed: dict[str, Any]) -> None:
    respx.get(f"{BASE_URL}release-group/{RG_MBID}").mock(
        return_value=httpx.Response(200, json={**RELEASE_GROUP, **malformed})
    )

    with pytest.raises(MetadataUnavailableError):
        make_provider(clock).get_release_group(RG_MBID)
