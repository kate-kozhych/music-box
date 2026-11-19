from __future__ import annotations

from unittest.mock import Mock

import pytest

from musicbox_core.apps.platform import tasks


@pytest.mark.parametrize(
    ("batches", "expected"),
    [([0], 0), ([tasks.BATCH_SIZE, 3], tasks.BATCH_SIZE + 3)],
)
def test_relay_task_stops_after_a_partial_or_empty_batch(
    monkeypatch: pytest.MonkeyPatch, batches: list[int], expected: int
) -> None:
    publish = Mock(side_effect=batches)
    monkeypatch.setattr(tasks, "publish_pending", publish)

    assert tasks.publish_outbox.run() == expected
    assert publish.call_count == len(batches)


def test_relay_task_yields_after_its_batch_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    publish = Mock(return_value=tasks.BATCH_SIZE)
    monkeypatch.setattr(tasks, "publish_pending", publish)

    assert tasks.publish_outbox.run() == tasks.MAX_BATCHES_PER_RUN * tasks.BATCH_SIZE
    assert publish.call_count == tasks.MAX_BATCHES_PER_RUN
