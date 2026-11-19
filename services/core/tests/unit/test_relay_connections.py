from __future__ import annotations

from unittest.mock import Mock

import pika.exceptions
import pytest

from musicbox_common.events import EXCHANGE
from musicbox_core.apps.platform import relay


@pytest.mark.parametrize("state", ["absent", "closed", "open", "close-fails"])
def test_reset_drops_the_cached_connection_even_when_close_fails(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    connection = None if state == "absent" else Mock(is_open=state != "closed")
    if connection is not None and state == "close-fails":
        connection.close.side_effect = pika.exceptions.AMQPConnectionError("already gone")
    monkeypatch.setattr(relay, "_connection", connection)

    relay.reset_connection()

    assert relay._connection is None
    if connection is not None:
        assert connection.close.call_count == int(state != "closed")


@pytest.mark.parametrize("state", ["absent", "closed", "open"])
def test_confirming_channel_reuses_only_an_open_connection(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    previous = None if state == "absent" else Mock(is_closed=state == "closed")
    replacement = Mock(is_closed=False)
    connect = Mock(return_value=replacement)
    monkeypatch.setattr(relay, "_connection", previous)
    monkeypatch.setattr(relay, "_connect", connect)

    channel = relay._confirming_channel()

    active = previous if state == "open" else replacement
    assert channel is active.channel()
    channel.exchange_declare.assert_called_once_with(EXCHANGE, exchange_type="topic", durable=True)
    channel.confirm_delivery.assert_called_once()
    assert connect.call_count == int(state != "open")


def test_relay_connection_has_bounded_heartbeats_and_blocked_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock()
    monkeypatch.setattr(relay.pika, "BlockingConnection", factory)

    assert relay._connect() is factory.return_value
    params = factory.call_args.args[0]
    assert params.heartbeat == 30
    assert params.blocked_connection_timeout == 30
