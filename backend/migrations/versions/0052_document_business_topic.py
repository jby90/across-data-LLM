"""Which part of the automotive cross-border data business a regulation serves, and how central it is:
the user's library, the country briefing and the citations are organised by it."""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("business_topic", sa.String(32), nullable=True))
    op.add_column("documents", sa.Column("business_weight", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("documents", "business_weight")
    op.drop_column("documents", "business_topic")
