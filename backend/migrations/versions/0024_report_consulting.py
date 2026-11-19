"""Consulting layer on report versions; existing reports stay unchanged (NULL)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "report_versions",
        sa.Column("consulting_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM report_versions WHERE consulting_json IS NOT NULL)
        THEN RAISE EXCEPTION 'retain consulting report history'; END IF; END $$;""")
    op.drop_column("report_versions", "consulting_json")
