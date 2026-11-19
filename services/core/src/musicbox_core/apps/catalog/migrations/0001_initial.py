import django.db.models.deletion
import musicbox_common.ids
import musicbox_core.apps.platform.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Album",
            fields=[
                (
                    "id",
                    musicbox_core.apps.platform.fields.BinaryUUIDField(
                        default=musicbox_common.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("mbid", models.CharField(max_length=36, unique=True)),
                ("title", models.CharField(max_length=500)),
                (
                    "primary_type",
                    models.CharField(choices=[("ALBUM", "Album"), ("EP", "Ep")], max_length=5),
                ),
                ("secondary_types", models.JSONField(blank=True, default=list)),
                ("first_release_date", models.DateField(blank=True, null=True)),
                (
                    "date_precision",
                    models.CharField(
                        blank=True,
                        choices=[("YEAR", "Year"), ("MONTH", "Month"), ("DAY", "Day")],
                        max_length=5,
                        null=True,
                    ),
                ),
                ("artist_credit", models.CharField(max_length=1000)),
                ("cover_url_250", models.CharField(blank=True, max_length=500, null=True)),
                ("cover_url_500", models.CharField(blank=True, max_length=500, null=True)),
                ("search_text", models.TextField(editable=False)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "album",
            },
        ),
        migrations.CreateModel(
            name="Genre",
            fields=[
                (
                    "id",
                    musicbox_core.apps.platform.fields.BinaryUUIDField(
                        default=musicbox_common.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("mbid", models.CharField(max_length=36, unique=True)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(max_length=100, unique=True)),
            ],
            options={
                "db_table": "genre",
            },
        ),
        migrations.CreateModel(
            name="Artist",
            fields=[
                (
                    "id",
                    musicbox_core.apps.platform.fields.BinaryUUIDField(
                        default=musicbox_common.ids.uuid7,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("mbid", models.CharField(max_length=36, unique=True)),
                ("name", models.CharField(max_length=500)),
                ("sort_name", models.CharField(max_length=500)),
                ("type", models.CharField(blank=True, default="", max_length=20)),
                ("country", models.CharField(blank=True, default="", max_length=2)),
                ("disambiguation", models.CharField(blank=True, default="", max_length=500)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
                ("discography_fetched_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "artist",
                "indexes": [models.Index(fields=["name"], name="artist_name_idx")],
            },
        ),
        migrations.CreateModel(
            name="AlbumArtist",
            fields=[
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "album_id",
                        "position",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("position", models.PositiveSmallIntegerField()),
                ("join_phrase", models.CharField(blank=True, default="", max_length=100)),
                (
                    "album",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="credits",
                        to="catalog.album",
                    ),
                ),
                (
                    "artist",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="credits",
                        to="catalog.artist",
                    ),
                ),
            ],
            options={
                "db_table": "album_artist",
                "ordering": ("album", "position"),
            },
        ),
        migrations.AddField(
            model_name="album",
            name="artists",
            field=models.ManyToManyField(
                related_name="albums", through="catalog.AlbumArtist", to="catalog.artist"
            ),
        ),
        migrations.CreateModel(
            name="AlbumGenre",
            fields=[
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "album_id",
                        "genre_id",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("votes", models.IntegerField(default=0)),
                (
                    "album",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="genre_tags",
                        to="catalog.album",
                    ),
                ),
                (
                    "genre",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.RESTRICT,
                        related_name="album_tags",
                        to="catalog.genre",
                    ),
                ),
            ],
            options={
                "db_table": "album_genre",
            },
        ),
        migrations.AddField(
            model_name="album",
            name="genres",
            field=models.ManyToManyField(
                related_name="albums", through="catalog.AlbumGenre", to="catalog.genre"
            ),
        ),
        migrations.CreateModel(
            name="AlbumRatingStats",
            fields=[
                (
                    "album",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.RESTRICT,
                        primary_key=True,
                        related_name="rating_stats",
                        serialize=False,
                        to="catalog.album",
                    ),
                ),
                ("rating_count", models.IntegerField(default=0)),
                ("rating_sum", models.IntegerField(default=0)),
                ("b01", models.IntegerField(default=0)),
                ("b02", models.IntegerField(default=0)),
                ("b03", models.IntegerField(default=0)),
                ("b04", models.IntegerField(default=0)),
                ("b05", models.IntegerField(default=0)),
                ("b06", models.IntegerField(default=0)),
                ("b07", models.IntegerField(default=0)),
                ("b08", models.IntegerField(default=0)),
                ("b09", models.IntegerField(default=0)),
                ("b10", models.IntegerField(default=0)),
            ],
            options={
                "db_table": "album_rating_stats",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("rating_count__gte", 0),
                            ("rating_sum__gte", 0),
                            ("b01__gte", 0),
                            ("b02__gte", 0),
                            ("b03__gte", 0),
                            ("b04__gte", 0),
                            ("b05__gte", 0),
                            ("b06__gte", 0),
                            ("b07__gte", 0),
                            ("b08__gte", 0),
                            ("b09__gte", 0),
                            ("b10__gte", 0),
                        ),
                        name="album_rating_stats_non_negative",
                    )
                ],
            },
        ),
        migrations.AddIndex(
            model_name="albumgenre",
            index=models.Index(fields=["genre", "album"], name="album_genre_genre_album_idx"),
        ),
        migrations.AddConstraint(
            model_name="albumgenre",
            constraint=models.CheckConstraint(
                condition=models.Q(("votes__gte", 0)), name="album_genre_votes_non_negative"
            ),
        ),
        migrations.AddIndex(
            model_name="album",
            index=models.Index(fields=["first_release_date"], name="album_first_release_date_idx"),
        ),
        migrations.AddConstraint(
            model_name="album",
            constraint=models.CheckConstraint(
                condition=models.Q(("primary_type__in", ["ALBUM", "EP"])),
                name="album_primary_type_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="album",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("date_precision__isnull", True), ("first_release_date__isnull", True)
                    ),
                    models.Q(
                        ("date_precision__isnull", False),
                        ("first_release_date__isnull", False),
                        models.Q(
                            ("date_precision", "DAY"),
                            models.Q(("date_precision", "MONTH"), ("first_release_date__day", 1)),
                            models.Q(
                                ("date_precision", "YEAR"),
                                ("first_release_date__day", 1),
                                ("first_release_date__month", 1),
                            ),
                            _connector="OR",
                        ),
                    ),
                    _connector="OR",
                ),
                name="album_release_date_precision_valid",
            ),
        ),
    ]
