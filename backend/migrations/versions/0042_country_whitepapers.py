"""Country white papers (the eight dimensions as a briefing), one stored version at a time."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "country_whitepapers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("country_code", sa.String(8), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("blocks", postgresql.JSONB, nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("report", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("country_code", "version_no", name="uq_whitepaper_version"),
    )
    op.create_index("ix_country_whitepapers_country_code", "country_whitepapers", ["country_code"])
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON country_whitepapers TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("country_whitepapers")
