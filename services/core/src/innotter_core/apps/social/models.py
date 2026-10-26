from __future__ import annotations

from django.db import models
from django.db.models import F, Q

from innotter_core.apps.users.models import UserProfile


class Follow(models.Model):
    pk = models.CompositePrimaryKey("follower_id", "followee_id")
    follower = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="following_edges")
    followee = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="follower_edges")
    created_at = models.DateTimeField()

    class Meta:
        db_table = "follow"
        constraints = [
            models.CheckConstraint(condition=~Q(follower=F("followee")), name="follow_not_self"),
        ]
        indexes = [
            models.Index(fields=["followee", "created_at"], name="follow_followee_created_idx"),
            models.Index(fields=["follower", "created_at"], name="follow_follower_created_idx"),
        ]
