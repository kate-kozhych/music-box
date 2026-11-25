from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

_handlers: defaultdict[type[Any], list[Callable[[Any], None]]] = defaultdict(list)


def subscribe[E](event_type: type[E], handler: Callable[[E], None]) -> None:
    if handler not in _handlers[event_type]:
        _handlers[event_type].append(handler)


def dispatch(event: object) -> None:
    for handler in _handlers[type(event)]:
        handler(event)
