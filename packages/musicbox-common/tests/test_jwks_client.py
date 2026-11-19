from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from musicbox_common.auth import JwksClient

URL = "http://identity:8000/.well-known/jwks.json"


def jwk(kid: str, key: Ed25519PrivateKey | None = None) -> dict[str, Any]:
    public = (key or Ed25519PrivateKey.generate()).public_key()
    raw = public.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
    }


def document(*kids: str) -> dict[str, Any]:
    return {"keys": [jwk(kid) for kid in kids]}


@respx.mock
def test_the_first_lookup_fetches_the_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("musicbox_common.auth.tokens.time.monotonic", lambda: 0.0)
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=document("k1")))

    key = JwksClient(URL).get_key("k1")

    assert key is not None
    assert route.call_count == 1


@respx.mock
def test_a_cached_key_is_served_without_a_second_request() -> None:
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=document("k1")))
    client = JwksClient(URL)

    assert client.get_key("k1") is not None
    assert client.get_key("k1") is not None

    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("uptime", [0.0, 1.0, 3599.0, 86400.0])
def test_an_unknown_kid_triggers_at_most_one_refetch_per_interval(
    monkeypatch: pytest.MonkeyPatch, uptime: float
) -> None:
    monkeypatch.setattr("musicbox_common.auth.tokens.time.monotonic", lambda: uptime)
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=document("k1")))
    client = JwksClient(URL, min_refetch_interval_s=3600.0)

    assert client.get_key("k2") is None
    assert client.get_key("k2") is None

    # A rotated-away kid must not let a caller hammer Identity (EXT/NFR-038).
    assert route.call_count == 1


@respx.mock
def test_a_rotated_key_is_picked_up_once_the_cache_has_expired() -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json=document("k1")),
            httpx.Response(200, json=document("k1", "k2")),
        ]
    )
    client = JwksClient(URL, cache_ttl_s=0.0, min_refetch_interval_s=0.0)

    assert client.get_key("k1") is not None
    assert client.get_key("k2") is not None
    assert route.call_count == 2


@respx.mock
def test_an_unknown_key_is_refetched_when_the_interval_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("musicbox_common.auth.tokens.time.monotonic", lambda: clock[0])
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json=document("k1")),
            httpx.Response(200, json=document("k1", "k2")),
        ]
    )
    client = JwksClient(URL, min_refetch_interval_s=10.0)

    assert client.get_key("k2") is None
    clock[0] = 9.99
    assert client.get_key("k2") is None
    assert route.call_count == 1

    clock[0] = 10.0
    assert client.get_key("k2") is not None
    assert route.call_count == 2


@respx.mock
def test_a_failing_endpoint_does_not_raise_and_keeps_the_cached_keys() -> None:
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(200, json=document("k1")),
            httpx.Response(503),
        ]
    )
    client = JwksClient(URL, cache_ttl_s=0.0, min_refetch_interval_s=0.0)
    assert client.get_key("k1") is not None

    assert client.get_key("k1") is not None
    assert route.call_count == 2


@respx.mock
def test_a_connection_error_is_swallowed() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("refused"))

    assert JwksClient(URL).get_key("k1") is None


@respx.mock
def test_a_non_json_body_is_swallowed() -> None:
    respx.get(URL).mock(return_value=httpx.Response(200, text="not json"))

    assert JwksClient(URL).get_key("k1") is None


@respx.mock
def test_keys_of_other_types_are_ignored() -> None:
    body = {
        "keys": [
            {"kty": "RSA", "kid": "rsa", "n": "x", "e": "AQAB"},
            {"kty": "OKP", "crv": "X25519", "kid": "x25519", "x": "x"},
            {"kty": "OKP", "crv": "Ed25519", "x": "missing-kid"},
            jwk("ed"),
        ]
    }
    respx.get(URL).mock(return_value=httpx.Response(200, json=body))
    client = JwksClient(URL)

    assert client.get_key("rsa") is None
    assert client.get_key("ed") is not None
