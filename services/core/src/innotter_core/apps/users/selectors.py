from __future__ import annotations

from innotter_common.auth import Principal
from innotter_common.errors import not_found
from innotter_core.apps.users.models import UserProfile, UserStatus


def visible_profile(username: str, viewer: Principal | None) -> UserProfile:
    profile = UserProfile.objects.filter(username=username.lower()).first()
    if profile is None or profile.status == UserStatus.DELETED:
        raise not_found("User not found.")
    if profile.status == UserStatus.BLOCKED and not (viewer and viewer.is_admin):
        raise not_found("User not found.")
    return profile
