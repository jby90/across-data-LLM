"""File review runs in the background; its state belongs to the conversation, kept in the database."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_attachments", sa.Column("review_state", sa.String(16), nullable=True))
    op.add_column("task_attachments", sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("task_attachments", sa.Column("review_error", sa.Text, nullable=True))


def downgrade():
    op.drop_column("task_attachments", "review_error")
    op.drop_column("task_attachments", "review_started_at")
    op.drop_column("task_attachments", "review_state")
