"""Gazette excerpt setting on a version: which instrument of the page becomes clauses."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_versions", sa.Column("excerpt", postgresql.JSONB, nullable=True))


def downgrade():
    op.drop_column("document_versions", "excerpt")
