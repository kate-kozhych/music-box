from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE album ADD FULLTEXT INDEX album_search_text_ft (search_text) WITH PARSER ngram",
            reverse_sql="ALTER TABLE album DROP INDEX album_search_text_ft",
        ),
    ]
