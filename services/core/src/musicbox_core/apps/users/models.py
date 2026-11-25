from __future__ import annotations

from typing import ClassVar

from django.db import models

from musicbox_core.apps.platform.fields import BinaryUUIDField


class UserStatus(models.TextChoices):
    ACTIVE = "active"
    BLOCKED = "blocked"
    DELETED = "deleted"


class UserProfile(models.Model):
    user_id = BinaryUUIDField(primary_key=True)
    username = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=50)
    bio = models.CharField(max_length=300, blank=True, default="")
    avatar_url = models.CharField(max_length=500, null=True, blank=True)
    status = models.CharField(max_length=10, choices=UserStatus.choices, default=UserStatus.ACTIVE)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    profile_version = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profile"

    def __str__(self) -> str:
        return self.username

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE


class DeletedUser(models.Model):
    user_id = BinaryUUIDField(primary_key=True)
    deleted_at = models.DateTimeField()
    erased_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "deleted_user"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["erased_at", "deleted_at"], name="deleted_user_pending_idx")
        ]
