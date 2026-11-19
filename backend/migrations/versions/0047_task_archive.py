"""Deleting a conversation archives it first: it can be restored or deleted for good from the archive."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_tasks", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analysis_tasks", sa.Column("archived_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_analysis_tasks_archived_at", "analysis_tasks", ["archived_at"])


def downgrade():
    op.drop_index("ix_analysis_tasks_archived_at", "analysis_tasks")
    op.drop_column("analysis_tasks", "archived_by")
    op.drop_column("analysis_tasks", "archived_at")
