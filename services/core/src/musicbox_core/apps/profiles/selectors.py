from __future__ import annotations

from dataclasses import dataclass

from musicbox_common.auth import Principal
from musicbox_core.apps.diary.selectors import count_entries
from musicbox_core.apps.posts.selectors import count_posts
from musicbox_core.apps.ratings.selectors import count_ratings
from musicbox_core.apps.reviews.selectors import count_reviews
from musicbox_core.apps.social import selectors as social
from musicbox_core.apps.social.models import Follow
from musicbox_core.apps.users.models import UserProfile
from musicbox_core.apps.users.selectors import visible_profile


@dataclass(frozen=True, slots=True)
class PublicProfile:
    user: UserProfile
    ratings: int
    reviews: int
    diary_entries: int
    posts: int
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
        ratings=count_ratings(user.user_id),
        reviews=count_reviews(user.user_id, viewer),
        diary_entries=count_entries(user.user_id, viewer),
        posts=count_posts(user.user_id, viewer),
        followers=social.count_followers(user, viewer),
        following=social.count_following(user, viewer),
        is_self=is_self,
        viewer_follows=viewer_follows,
    )
