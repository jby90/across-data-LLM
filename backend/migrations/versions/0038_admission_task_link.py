"""Admission project: optional link to the assessment it came from, and its own answers."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admission_projects",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_admission_projects_task_id", "admission_projects", ["task_id"])
    op.add_column(
        "admission_projects",
        sa.Column("facts", postgresql.JSONB, nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("admission_projects", "facts")
    op.drop_index("ix_admission_projects_task_id", table_name="admission_projects")
    op.drop_column("admission_projects", "task_id")
