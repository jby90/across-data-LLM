"""Store verified foreign-language formal application letter on each admission draft.

``admission_drafts.foreign_draft`` holds the translated and reverse-verified Part 1 formal
application blocks in the authority's official language (e.g. English, French, Arabic)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admission_drafts",
        sa.Column("foreign_draft", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )


def downgrade():
    op.drop_column("admission_drafts", "foreign_draft")
