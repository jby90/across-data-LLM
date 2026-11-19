"""Admission handling follows four stages common to every country (prepare materials, submit,
review and corrections, result). The platform works out the first from the material draft; the
user records only what happens outside the platform — when and how it was submitted, and the
outcome — kept here per project. The per-step ledger (admission_progress) is no longer written."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admission_projects",
        sa.Column("milestones", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade():
    op.drop_column("admission_projects", "milestones")
