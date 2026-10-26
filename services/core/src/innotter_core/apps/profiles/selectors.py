from __future__ import annotations

from dataclasses import dataclass

from innotter_common.auth import Principal
from innotter_core.apps.social import selectors as social
from innotter_core.apps.social.models import Follow
from innotter_core.apps.users.models import UserProfile
from innotter_core.apps.users.selectors import visible_profile


@dataclass(frozen=True, slots=True)
class PublicProfile:
    user: UserProfile
    followers: int
    following: int
    is_self: bool
    viewer_follows: bool | None


def public_profile(username: str, viewer: Principal | None) -> PublicProfile:
    user = visible_profile(username, viewer)
    is_self = viewer is not None and viewer.user_id == user.user_id
    viewer_follows = None
    if viewer is not None and not is_self:
        viewer_follows = Follow.objects.filter(follower_id=viewer.user_id, followee=user).exists()
    return PublicProfile(
        user=user,
        followers=social.count_followers(user, viewer),
        following=social.count_following(user, viewer),
        is_self=is_self,
        viewer_follows=viewer_follows,
    )
