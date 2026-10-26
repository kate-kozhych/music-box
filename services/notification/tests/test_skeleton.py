import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "notifier",
        "notifier.inbox",
        "notifier.notifications",
        "notifier.snapshots",
        "notifier.channels",
    ],
)
def test_module_is_importable(module: str) -> None:
    assert importlib.import_module(module) is not None
