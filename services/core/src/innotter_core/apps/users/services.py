from __future__ import annotations

import logging
import uuid
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from innotter_common.auth import Principal
from innotter_common.events import (
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
)
from innotter_core.apps.users.models import UserProfile, UserStatus

log = logging.getLogger(__name__)


def ensure_profile(principal: Principal) -> UserProfile:
    profile = UserProfile.objects.filter(user_id=principal.user_id).first()
    if profile is not None:
        return profile
    try:
        with transaction.atomic():
            return UserProfile.objects.create(
                user_id=principal.user_id,
                username=principal.username,
                display_name=principal.username,
                profile_version=0,
                joined_at=timezone.now(),
            )
    except IntegrityError:
        return UserProfile.objects.get(user_id=principal.user_id)


def apply_registered(event: UserRegisteredV1, occurred_at: datetime) -> None:
    profile = UserProfile.objects.select_for_update().filter(user_id=event.user_id).first()
    if profile is None:
        UserProfile.objects.create(
            user_id=event.user_id,
            username=event.username,
            display_name=event.display_name,
            avatar_url=event.avatar_url,
            profile_version=event.profile_version,
            joined_at=occurred_at,
        )
        return
    if event.profile_version > profile.profile_version:
        profile.username = event.username
        profile.display_name = event.display_name
        profile.avatar_url = event.avatar_url
        profile.profile_version = event.profile_version
        profile.joined_at = min(profile.joined_at, occurred_at)
        profile.save()


def apply_profile_updated(event: UserProfileUpdatedV1) -> None:
    profile = UserProfile.objects.select_for_update().filter(user_id=event.user_id).first()
    if profile is None:
        log.warning("profile_updated for unknown user", extra={"user_id": str(event.user_id)})
        return
    if event.profile_version > profile.profile_version:
        profile.display_name = event.display_name
        profile.bio = event.bio
        profile.avatar_url = event.avatar_url
        profile.profile_version = event.profile_version
        profile.save(update_fields=["display_name", "bio", "avatar_url", "profile_version", "updated_at"])


def apply_status_changed(event: UserStatusChangedV1) -> None:
    updated = (
        UserProfile.objects.filter(user_id=event.user_id)
        .exclude(status=UserStatus.DELETED)
        .update(status=event.status, updated_at=timezone.now())
    )
    if not updated:
        log.warning("status_changed for unknown user", extra={"user_id": str(event.user_id)})


def tombstone_username(user_id: uuid.UUID) -> str:
    return f"~{user_id.hex}"


def apply_deleted(event: UserDeletedV1) -> None:
    UserProfile.objects.filter(user_id=event.user_id).update(
        status=UserStatus.DELETED,
        username=tombstone_username(event.user_id),
        display_name="[deleted]",
        bio="",
        avatar_url=None,
        updated_at=timezone.now(),
    )
