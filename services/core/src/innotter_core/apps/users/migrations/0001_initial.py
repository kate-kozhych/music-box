from django.db import migrations, models

import innotter_core.apps.platform.fields


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                (
                    "user_id",
                    innotter_core.apps.platform.fields.BinaryUUIDField(
                        primary_key=True, serialize=False
                    ),
                ),
                ("username", models.CharField(max_length=64, unique=True)),
                ("display_name", models.CharField(max_length=50)),
                ("bio", models.CharField(blank=True, default="", max_length=300)),
                ("avatar_url", models.CharField(blank=True, max_length=500, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("blocked", "Blocked"),
                            ("deleted", "Deleted"),
                        ],
                        default="active",
                        max_length=10,
                    ),
                ),
                ("profile_version", models.PositiveIntegerField(default=0)),
                ("joined_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "user_profile",
            },
        ),
    ]
