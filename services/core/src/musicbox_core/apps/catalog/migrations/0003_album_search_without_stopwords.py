from django.db import migrations

DROP_INDEX = "ALTER TABLE album DROP INDEX album_search_text_ft"
ADD_INDEX = "ALTER TABLE album ADD FULLTEXT INDEX album_search_text_ft (search_text) WITH PARSER ngram"
RESTORE_STOPWORDS = "SET SESSION innodb_ft_enable_stopword = DEFAULT"


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_album_search_fulltext"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "SET SESSION innodb_ft_enable_stopword = OFF",
                DROP_INDEX,
                ADD_INDEX,
                RESTORE_STOPWORDS,
            ],
            reverse_sql=[
                "SET SESSION innodb_ft_enable_stopword = ON",
                DROP_INDEX,
                ADD_INDEX,
                RESTORE_STOPWORDS,
            ],
        ),
    ]
