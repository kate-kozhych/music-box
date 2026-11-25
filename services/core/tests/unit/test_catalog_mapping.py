from __future__ import annotations

import pytest
from tests.fake_redis import FakeRedis

from musicbox_core.apps.catalog import mapping
from musicbox_core.apps.catalog.cache import SearchCache, cache_key
from musicbox_core.apps.catalog.models import DatePrecision, PrimaryType
from musicbox_core.apps.catalog.ports import ReleaseGroupSummary, UnsupportedReleaseTypeError
from musicbox_core.apps.catalog.selectors import boolean_query
from musicbox_core.apps.catalog.throttle import CircuitBreaker, RedisRateLimiter


@pytest.mark.parametrize(
    ("raw", "expected", "precision"),
    [
        ("1997", (1997, 1, 1), DatePrecision.YEAR),
        ("1997-09", (1997, 9, 1), DatePrecision.MONTH),
        ("1997-09-22", (1997, 9, 22), DatePrecision.DAY),
    ],
)
def test_partial_dates_keep_their_precision(
    raw: str, expected: tuple[int, int, int], precision: DatePrecision
) -> None:
    date, got = mapping.parse_partial_date(raw)
    assert date is not None
    assert (date.year, date.month, date.day) == expected
    assert got == precision


@pytest.mark.parametrize("raw", ["", None, "not-a-date", "1997-13-40"])
def test_unusable_dates_become_null(raw: str | None) -> None:
    assert mapping.parse_partial_date(raw) == (None, None)


def test_album_and_ep_are_supported() -> None:
    assert mapping.primary_type_of({"primary-type": "Album"}) == PrimaryType.ALBUM
    assert mapping.primary_type_of({"primary-type": "EP"}) == PrimaryType.EP


@pytest.mark.parametrize("value", ["Single", "Broadcast", "Other", None])
def test_other_types_are_rejected(value: str | None) -> None:
    with pytest.raises(UnsupportedReleaseTypeError):
        mapping.primary_type_of({"primary-type": value})


def test_multi_artist_credits_keep_their_order_and_join_phrases() -> None:
    credit_entries = [
        {
            "name": "Ann",
            "joinphrase": " & ",
            "artist": {"id": "11111111-1111-4111-8111-111111111111", "name": "Ann"},
        },
        {
            "name": "Bo",
            "joinphrase": "",
            "artist": {"id": "22222222-2222-4222-8222-222222222222", "name": "Bo"},
        },
    ]
    assert mapping.artist_credit_string(credit_entries) == "Ann & Bo"
    assert [r.name for r in mapping.artist_refs(credit_entries)] == ["Ann", "Bo"]


def test_genres_are_capped_at_ten_highest_votes_first() -> None:
    payload = {
        "genres": [
            {"id": f"{i:08d}-0000-4000-8000-000000000000", "name": f"g{i}", "count": i}
            for i in range(15)
        ]
    }
    genres = mapping.genre_refs(payload)
    assert len(genres) == 10
    assert [g.votes for g in genres] == sorted((g.votes for g in genres), reverse=True)


def test_lucene_metacharacters_are_escaped() -> None:
    assert mapping.escape_lucene('a:b (c) "d"') == 'a\\:b \\(c\\) \\"d\\"'


def test_boolean_query_requires_every_token_and_quotes_it() -> None:
    assert boolean_query("Blue  Train") == '+"blue" +"train"'


def test_boolean_query_folds_accents_and_case() -> None:
    assert boolean_query("BJÖRK") == '+"bjork"'


def test_boolean_query_drops_tokens_below_the_ngram_size() -> None:
    assert boolean_query("a of") == '+"of"'
    assert boolean_query("a") == ""


def test_boolean_query_splits_on_punctuation() -> None:
    assert boolean_query("What's +going (on)") == '+"what" +"going" +"on"'
    assert boolean_query("!!!") == ""


def test_only_one_caller_holds_the_slot_per_second() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, sleep=redis.advance, monotonic=lambda: redis.now)

    assert limiter.acquire(0.5) is True
    assert limiter.acquire(0.5) is False
    redis.advance(1.0)
    assert limiter.acquire(0.5) is True


def test_a_waiting_caller_gets_the_next_slot() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, sleep=redis.advance, monotonic=lambda: redis.now)
    limiter.acquire(0.5)
    assert limiter.acquire(3.0) is True
    assert redis.now >= 1.0


def test_the_breaker_opens_on_the_fifth_failure_and_recovers() -> None:
    redis = FakeRedis()
    breaker = CircuitBreaker(redis)
    for _ in range(4):
        breaker.record_failure()
    assert breaker.is_open() is False
    breaker.record_failure()
    assert breaker.is_open() is True

    redis.advance(61)
    assert breaker.is_open() is False


def summary(mbid: str) -> ReleaseGroupSummary:
    return ReleaseGroupSummary(
        mbid=mbid, title="T", primary_type=PrimaryType.ALBUM, artist_credit="A"
    )


def test_equivalent_queries_share_one_cache_entry() -> None:
    assert cache_key("  Blue   TRAIN ") == cache_key("blue train")


def test_the_cache_round_trips_and_expires() -> None:
    redis = FakeRedis()
    cache = SearchCache(redis)
    rows = [summary("f5093c06-23e3-404f-aeaa-40f72885ee3a")]

    assert cache.get("blue train") is None
    cache.set("blue train", rows)
    cached = cache.get("blue train")
    assert cached is not None
    assert [r.mbid for r in cached] == [r.mbid for r in rows]

    redis.advance(24 * 60 * 60 + 1)
    assert cache.get("blue train") is None


def test_a_cached_empty_result_is_not_a_miss() -> None:
    cache = SearchCache(FakeRedis())
    cache.set("nothing here", [])
    assert cache.get("nothing here") == []
