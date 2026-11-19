"""Fixed facts of an admission item read from its law (local testing, local representative,
application language, validity — each "unknown" when the law does not say), and the approval an
enterprise obtained for a filing project (number, dates, file) for the expiry reminder."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admission_entries", sa.Column("profile", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("admission_projects", sa.Column("approval", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade():
    op.drop_column("admission_projects", "approval")
    op.drop_column("admission_entries", "profile")
