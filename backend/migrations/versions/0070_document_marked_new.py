"""Administrators can mark a first-collected law (no publication or effective date) as newly issued.

``documents.news_marked_at`` / ``news_marked_by``: when and by whom. A marked law is pushed once to the
enterprises whose subscription matches (same notice as any newly collected law); one law can be marked
only once."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("news_marked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("news_marked_by", UUID(as_uuid=True), nullable=True))


def downgrade():
    op.drop_column("documents", "news_marked_by")
    op.drop_column("documents", "news_marked_at")
