import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Follow",
            fields=[
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "follower_id",
                        "followee_id",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField()),
                (
                    "followee",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="follower_edges",
                        to="users.userprofile",
                    ),
                ),
                (
                    "follower",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="following_edges",
                        to="users.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "follow",
                "indexes": [
                    models.Index(
                        fields=["followee", "created_at"], name="follow_followee_created_idx"
                    ),
                    models.Index(
                        fields=["follower", "created_at"], name="follow_follower_created_idx"
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("follower", models.F("followee")), _negated=True),
                        name="follow_not_self",
                    )
                ],
            },
        ),
    ]
