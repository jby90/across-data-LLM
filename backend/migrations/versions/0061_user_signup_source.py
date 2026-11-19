"""Where an enterprise account came from: opened by the platform administrator, or registered
through the portal. Existing accounts were all opened by the administrator."""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users",
        sa.Column("signup_source", sa.String(16), nullable=False, server_default="admin"),
    )


def downgrade():
    op.drop_column("users", "signup_source")
