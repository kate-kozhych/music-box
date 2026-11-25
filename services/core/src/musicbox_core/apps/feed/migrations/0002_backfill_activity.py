from typing import Any

from django.db import migrations

BACKFILL = (
    """
    INSERT INTO activity (id, actor_id, verb, album_id, post_id, is_visible, created_at)
    SELECT id, user_id, 'POST', album_id, id, hidden_at IS NULL, created_at FROM post
    WHERE id NOT IN (SELECT post_id FROM activity WHERE post_id IS NOT NULL)
    """,
    """
    INSERT INTO activity (id, actor_id, verb, album_id, review_id, is_visible, created_at)
    SELECT id, user_id, 'REVIEW', album_id, id, hidden_at IS NULL, created_at FROM review
    WHERE id NOT IN (SELECT review_id FROM activity WHERE review_id IS NOT NULL)
    """,
    """
    INSERT INTO activity (id, actor_id, verb, album_id, diary_entry_id, is_visible, created_at)
    SELECT id, user_id, 'DIARY', album_id, id, hidden_at IS NULL, created_at FROM diary_entry
    WHERE visibility = 'public'
      AND id NOT IN (SELECT diary_entry_id FROM activity WHERE diary_entry_id IS NOT NULL)
    """,
    """
    INSERT INTO activity (id, actor_id, verb, album_id, rating_id, is_visible, created_at)
    SELECT id, user_id, 'RATING', album_id, id, TRUE, created_at FROM rating
    WHERE id NOT IN (SELECT rating_id FROM activity WHERE rating_id IS NOT NULL)
      AND NOT EXISTS (
        SELECT 1 FROM review
        WHERE review.user_id = rating.user_id AND review.album_id = rating.album_id
      )
      AND NOT EXISTS (
        SELECT 1 FROM diary_entry
        WHERE diary_entry.user_id = rating.user_id
          AND diary_entry.album_id = rating.album_id
          AND diary_entry.rating_value IS NOT NULL
          AND diary_entry.created_at <= rating.created_at
          AND diary_entry.created_at > rating.created_at - INTERVAL 1 SECOND
      )
    """,
)


def backfill(apps: Any, schema_editor: Any) -> None:
    with schema_editor.connection.cursor() as cursor:
        for statement in BACKFILL:
            cursor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("feed", "0001_initial")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
