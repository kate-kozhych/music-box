from __future__ import annotations

from innotter_common.events import (
    USER_DELETED,
    USER_PROFILE_UPDATED,
    USER_REGISTERED,
    USER_STATUS_CHANGED,
    Envelope,
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
)
from innotter_core.apps.platform.messaging import Handler, QueueSpec
from innotter_core.apps.users import services

QUEUE = QueueSpec(name="core.identity-events", bindings=("identity.user.#",))


def _registered(envelope: Envelope) -> None:
    services.apply_registered(UserRegisteredV1.model_validate(envelope.payload), envelope.occurred_at)


def _profile_updated(envelope: Envelope) -> None:
    services.apply_profile_updated(UserProfileUpdatedV1.model_validate(envelope.payload))


def _status_changed(envelope: Envelope) -> None:
    services.apply_status_changed(UserStatusChangedV1.model_validate(envelope.payload))


def _deleted(envelope: Envelope) -> None:
    services.apply_deleted(UserDeletedV1.model_validate(envelope.payload))


HANDLERS: dict[str, Handler] = {
    USER_REGISTERED: _registered,
    USER_PROFILE_UPDATED: _profile_updated,
    USER_STATUS_CHANGED: _status_changed,
    USER_DELETED: _deleted,
}
