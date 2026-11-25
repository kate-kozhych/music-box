from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db.models import Q

from musicbox_common.pagination import Cursor
from musicbox_core.apps.moderation.models import ModerationAction
from musicbox_core.apps.platform.cursors import cursor_uuid
from musicbox_core.apps.users.models import UserProfile


@dataclass(frozen=True, slots=True)
class LogPage:
    items: Sequence[ModerationAction]
    next_cursor: str | None


def moderation_log(*, limit: int, cursor: str | None) -> LogPage:
    actions = ModerationAction.objects.all()
    if cursor:
        position = Cursor.decode(cursor)
        after = cursor_uuid(position)
        actions = actions.filter(
            Q(created_at__lt=position.t) | Q(created_at=position.t, id__lt=after)
        )
    rows = list(actions.order_by("-created_at", "-id")[: limit + 1])
    items = rows[:limit]
    next_cursor = (
        Cursor(t=items[-1].created_at, id=str(items[-1].id)).encode() if len(rows) > limit else None
    )
    return LogPage(items, next_cursor)


def log_payloads(actions: Sequence[ModerationAction]) -> list[dict[str, Any]]:
    admins = {
        profile.user_id: profile
        for profile in UserProfile.objects.filter(
            user_id__in={action.admin_id for action in actions}
        )
    }
    return [
        {
            "id": str(action.id),
            "admin_id": action.admin_id,
            "admin": admins.get(action.admin_id),
            "target_type": action.target_type,
            "target_id": str(action.target_id),
            "action": action.action,
            "reason": action.reason,
            "created_at": action.created_at,
        }
        for action in actions
    ]
