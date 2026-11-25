from typing import Any

from django.db import migrations, models
from django.utils import timezone

import musicbox_core.apps.platform.fields


def record_deleted_users(apps: Any, schema_editor: Any) -> None:
    profiles = apps.get_model("users", "UserProfile").objects.filter(status="deleted")
    apps.get_model("users", "DeletedUser").objects.bulk_create(
        apps.get_model("users", "DeletedUser")(
            user_id=profile.user_id,
            deleted_at=profile.status_changed_at or timezone.now(),
        )
        for profile in profiles
    )


class Migration(migrations.Migration):
    dependencies = [("users", "0002_user_profile_status_changed_at")]

    operations = [
        migrations.CreateModel(
            name="DeletedUser",
            fields=[
                (
                    "user_id",
                    musicbox_core.apps.platform.fields.BinaryUUIDField(
                        primary_key=True, serialize=False
                    ),
                ),
                ("deleted_at", models.DateTimeField()),
                ("erased_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "deleted_user",
                "indexes": [
                    models.Index(
                        fields=["erased_at", "deleted_at"],
                        name="deleted_user_pending_idx",
                    )
                ],
            },
        ),
        migrations.RunPython(record_deleted_users, migrations.RunPython.noop),
    ]
