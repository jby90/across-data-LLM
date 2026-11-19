"""Public-opinion sources and items (kept apart from the legal library)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "opinion_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("publisher", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("entry_url", sa.String(1024), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_status", sa.String(16)),
        sa.Column("last_note", sa.Text),
    )
    op.create_table(
        "opinion_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opinion_sources.id", ondelete="SET NULL"),
        ),
        sa.Column("publisher", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("published_on", sa.Date),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("matched_keywords", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("level", sa.String(8)),
        sa.Column("rules", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("bases", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("facts", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("scale_unknown", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("graded_at", sa.DateTime(timezone=True)),
        sa.Column("grading_version", sa.String(32)),
    )
    op.create_index("ix_opinion_items_level_date", "opinion_items", ["level", "published_on"])
    op.create_index("ix_opinion_items_fetched", "opinion_items", ["fetched_at"])
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON opinion_sources, opinion_items TO cyshuhe_app;
        END IF; END $$;""")


def downgrade():
    op.drop_table("opinion_items")
    op.drop_table("opinion_sources")
