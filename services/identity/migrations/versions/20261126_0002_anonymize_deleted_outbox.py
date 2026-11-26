from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE outbox AS o
        SET payload = o.payload || jsonb_build_object(
            'display_name', '[deleted]', 'avatar_url', NULL,
            'username', a.username::text
        )
        FROM accounts AS a
        WHERE a.status = 'deleted' AND o.payload->>'user_id' = a.id::text
          AND o.type = 'identity.user.registered.v1'
    """)
    op.execute("""
        UPDATE outbox AS o
        SET payload = o.payload || jsonb_build_object(
            'display_name', '[deleted]', 'bio', '', 'avatar_url', NULL
        )
        FROM accounts AS a
        WHERE a.status = 'deleted' AND o.payload->>'user_id' = a.id::text
          AND o.type = 'identity.user.profile_updated.v1'
    """)


def downgrade() -> None:
    pass
