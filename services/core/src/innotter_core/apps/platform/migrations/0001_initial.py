from django.db import migrations, models

import innotter_core.apps.platform.fields


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="InboxEvent",
            fields=[
                (
                    "event_id",
                    innotter_core.apps.platform.fields.BinaryUUIDField(
                        primary_key=True, serialize=False
                    ),
                ),
                ("type", models.CharField(max_length=100)),
                ("processed_at", models.DateTimeField()),
            ],
            options={
                "db_table": "inbox",
            },
        ),
        migrations.CreateModel(
            name="OutboxEvent",
            fields=[
                (
                    "event_id",
                    innotter_core.apps.platform.fields.BinaryUUIDField(
                        primary_key=True, serialize=False
                    ),
                ),
                ("type", models.CharField(max_length=100)),
                ("payload", models.JSONField()),
                ("correlation_id", models.CharField(blank=True, max_length=128, null=True)),
                ("occurred_at", models.DateTimeField()),
                ("published_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "outbox",
                "indexes": [
                    models.Index(fields=["published_at"], name="outbox_published_at_idx")
                ],
            },
        ),
    ]
