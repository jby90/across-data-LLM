"""The passages of a scanned law that may have been misread, one by one, for the administrator to
confirm or correct next to the page image: [{clause_id, page, state: pending | ok | fixed}]."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_versions", sa.Column("check_items", JSONB(), nullable=True))


def downgrade():
    op.drop_column("document_versions", "check_items")
