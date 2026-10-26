from __future__ import annotations

from django.db import models

from innotter_core.apps.platform.fields import BinaryUUIDField


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
