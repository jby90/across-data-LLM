"""A conversation title the user chose is never replaced by an automatic one."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_tasks", sa.Column("title_custom", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("analysis_tasks", "title_custom")
