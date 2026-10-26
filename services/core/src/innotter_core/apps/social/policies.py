from __future__ import annotations

from innotter_common.errors import AppError, ErrorCode, not_found
from innotter_core.apps.users.models import UserProfile


def ensure_can_follow(actor: UserProfile, target: UserProfile) -> None:
    if target.user_id == actor.user_id:
        raise AppError(422, ErrorCode.SELF_FOLLOW, "You can't follow yourself.")
    if not target.is_active:
        raise not_found("User not found.")
