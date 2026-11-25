from __future__ import annotations

import uuid
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from musicbox_common.auth import Principal
from musicbox_common.errors import ErrorCode, unauthorized
from musicbox_common.events import (
    UserDeletedV1,
    UserProfileUpdatedV1,
    UserRegisteredV1,
    UserStatusChangedV1,
)
from musicbox_core.apps.platform.domain_events import dispatch
from musicbox_core.apps.users.events import UserVisibilityChanged
from musicbox_core.apps.users.models import UserProfile, UserStatus


def ensure_profile(principal: Principal) -> UserProfile:
    profile = UserProfile.objects.filter(user_id=principal.user_id).first()
    if profile is None:
        profile = _create_from_claims(principal)
    if _awaits_registration(profile):
        UserProfile.objects.filter(
            user_id=profile.user_id, username=profile.username, status=UserStatus.ACTIVE
        ).update(username=principal.username)
        profile.refresh_from_db()
    return profile


def lock_active_profile(principal: Principal) -> UserProfile:
    ensure_profile(principal)
    profile = UserProfile.objects.select_for_update().get(user_id=principal.user_id)
    if not profile.is_active:
        raise unauthorized(ErrorCode.TOKEN_INVALID, "The account is not active.")
    return profile


def _create_from_claims(principal: Principal) -> UserProfile:
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


def _awaits_registration(profile: UserProfile) -> bool:
    return profile.status != UserStatus.DELETED and profile.username == tombstone_username(
        profile.user_id
    )


def _locked_profile(user_id: uuid.UUID) -> UserProfile:
    profile, _ = UserProfile.objects.select_for_update().get_or_create(
        user_id=user_id,
        defaults={
            "username": tombstone_username(user_id),
            "display_name": "",
            "profile_version": 0,
            "joined_at": timezone.now(),
        },
    )
    return profile


def apply_registered(event: UserRegisteredV1, occurred_at: datetime) -> None:
    profile = _locked_profile(event.user_id)
    if profile.status == UserStatus.DELETED:
        return
    newer = event.profile_version > profile.profile_version
    if not newer and not _awaits_registration(profile):
        return
    profile.username = event.username
    profile.joined_at = min(profile.joined_at, occurred_at)
    if newer:
        profile.display_name = event.display_name
        profile.avatar_url = event.avatar_url
        profile.profile_version = event.profile_version
    profile.save()


def apply_profile_updated(event: UserProfileUpdatedV1) -> None:
    profile = _locked_profile(event.user_id)
    if profile.status == UserStatus.DELETED or event.profile_version <= profile.profile_version:
        return
    profile.display_name = event.display_name
    profile.bio = event.bio
    profile.avatar_url = event.avatar_url
    profile.profile_version = event.profile_version
    profile.save()


def apply_status_changed(event: UserStatusChangedV1) -> None:
    profile = _locked_profile(event.user_id)
    if profile.status == UserStatus.DELETED:
        return
    if profile.status_changed_at is not None and event.changed_at <= profile.status_changed_at:
        return
    changed = profile.status != event.status
    profile.status = event.status
    profile.status_changed_at = event.changed_at
    profile.save()
    if changed:
        dispatch(UserVisibilityChanged(event.user_id))


def tombstone_username(user_id: uuid.UUID) -> str:
    return f"~{user_id.hex}"


def apply_deleted(event: UserDeletedV1) -> None:
    profile = _locked_profile(event.user_id)
    profile.status = UserStatus.DELETED
    profile.status_changed_at = event.deleted_at
    profile.username = tombstone_username(event.user_id)
    profile.display_name = "[deleted]"
    profile.bio = ""
    profile.avatar_url = None
    profile.save()
    dispatch(UserVisibilityChanged(event.user_id))
