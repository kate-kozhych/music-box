from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from innotter_common.auth import Principal
from innotter_common.errors import not_found
from innotter_common.events import FOLLOW_CREATED, FollowCreatedV1
from innotter_core.apps.platform.outbox import record_event
from innotter_core.apps.social.models import Follow
from innotter_core.apps.social.policies import ensure_can_follow
from innotter_core.apps.users.models import UserProfile
from innotter_core.apps.users.services import ensure_profile


def _target(username: str) -> UserProfile:
    target = UserProfile.objects.filter(username=username.lower()).first()
    if target is None:
        raise not_found("User not found.")
    return target


def follow(principal: Principal, username: str) -> bool:
    actor = ensure_profile(principal)
    target = _target(username)
    ensure_can_follow(actor, target)
    try:
        with transaction.atomic():
            _, created = Follow.objects.get_or_create(
                follower=actor, followee=target, defaults={"created_at": timezone.now()}
            )
            if created:
                record_event(
                    FOLLOW_CREATED,
                    FollowCreatedV1(follower_id=actor.user_id, followee_id=target.user_id),
                )
    except IntegrityError:
        return False
    return created


def unfollow(principal: Principal, username: str) -> bool:
    actor = ensure_profile(principal)
    target = UserProfile.objects.filter(username=username.lower()).first()
    if target is None or target.status == "deleted":
        raise not_found("User not found.")
    deleted, _ = Follow.objects.filter(follower=actor, followee=target).delete()
    return deleted > 0
