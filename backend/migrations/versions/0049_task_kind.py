"""A task is either a consulting chat or a compliance-documents project (no assessment run)."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_tasks", sa.Column("kind", sa.String(16), nullable=False, server_default="chat"))
    op.create_index("ix_analysis_tasks_kind", "analysis_tasks", ["kind"])


def downgrade():
    op.drop_index("ix_analysis_tasks_kind", "analysis_tasks")
    op.drop_column("analysis_tasks", "kind")
