"""Directory of standards (directory information only; the standards' text is not stored)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "standard_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("number", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("published_on", sa.Date),
        sa.Column("effective_on", sa.Date),
        sa.Column("publisher", sa.String(128), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=False),
        sa.Column("detail_url", sa.String(1024)),
        sa.Column("topics", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("matched_keywords", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_standard_entries_country_status", "standard_entries", ["country_code", "status"]
    )
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON standard_entries TO cyshuhe_app;
        END IF; END $$;""")


def downgrade():
    op.drop_table("standard_entries")
