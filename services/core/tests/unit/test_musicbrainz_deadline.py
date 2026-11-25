from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from musicbox_core.apps.catalog.musicbrainz import MusicBrainzProvider
from musicbox_core.apps.catalog.ports import MetadataUnavailableError

RG_MBID = "f5093c06-23e3-404f-aeaa-40f72885ee3a"
BUDGET = 0.6
TOLERANCE = 0.5
DRIP_SECONDS = 0.05
HEADERS = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100000\r\n"


def drip_headers(connection: socket.socket, stopped: threading.Event) -> None:
    connection.sendall(HEADERS + b"X-Slow: ")
    while not stopped.wait(DRIP_SECONDS):
        connection.sendall(b"x")


def drip_body(connection: socket.socket, stopped: threading.Event) -> None:
    connection.sendall(HEADERS + b"\r\n")
    while not stopped.wait(DRIP_SECONDS):
        connection.sendall(b" ")


def stay_silent(connection: socket.socket, stopped: threading.Event) -> None:
    stopped.wait()


Behaviour = Callable[[socket.socket, threading.Event], None]


@pytest.fixture
def behaviour() -> Behaviour:
    return stay_silent


@pytest.fixture
def server(behaviour: Behaviour) -> Iterator[str]:
    stopped = threading.Event()
    with socket.create_server(("127.0.0.1", 0)) as listener:

        def serve() -> None:
            while not stopped.is_set():
                with contextlib.suppress(OSError):
                    connection, _ = listener.accept()
                    with connection:
                        connection.recv(65536)
                        behaviour(connection, stopped)

        threading.Thread(target=serve, daemon=True).start()
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/"
        stopped.set()


def running_calls() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "musicbrainz-call"]


@pytest.mark.parametrize("behaviour", [drip_headers, drip_body, stay_silent])
def test_nfr063_a_slowly_streamed_response_ends_at_the_total_deadline(server: str) -> None:
    provider = MusicBrainzProvider(user_agent="test", base_url=server, total_budget=BUDGET)
    started = time.monotonic()

    with pytest.raises(MetadataUnavailableError):
        provider.get_release_group(RG_MBID)

    assert time.monotonic() - started < BUDGET + TOLERANCE
    for call in running_calls():
        call.join(timeout=TOLERANCE)
    assert running_calls() == []


def test_nfr063_a_stuck_limiter_ends_at_the_total_deadline_without_a_late_request(
    server: str,
) -> None:
    released = threading.Event()

    class StuckLimiter:
        def acquire(self, timeout_s: float = 3.0) -> bool:
            return released.wait()

    class Breaker:
        failures = 0
        open_seconds = 60

        def is_open(self) -> bool:
            return False

        def record_success(self) -> None: ...

        def record_failure(self) -> None:
            Breaker.failures += 1

    provider = MusicBrainzProvider(
        user_agent="test",
        base_url=server,
        total_budget=BUDGET,
        limiter=StuckLimiter(),
        breaker=Breaker(),  # type: ignore[arg-type]
    )
    started = time.monotonic()

    with pytest.raises(MetadataUnavailableError):
        provider.get_release_group(RG_MBID)

    assert time.monotonic() - started < BUDGET + TOLERANCE
    released.set()
    for call in running_calls():
        call.join(timeout=TOLERANCE)
    assert running_calls() == []
    assert Breaker.failures == 0
