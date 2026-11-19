"""Short model-written texts for the country page (overview lines and one sentence per dimension)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "country_page_texts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("country_code", sa.String(8), nullable=False),
        sa.Column("data", postgresql.JSONB, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_country_page_texts_country_code", "country_page_texts", ["country_code"])
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON country_page_texts TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("country_page_texts")
