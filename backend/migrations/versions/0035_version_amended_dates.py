"""Amendment dates a legal text states about itself (ontology attribute: revision time)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_versions",
        sa.Column("amended_dates", postgresql.ARRAY(sa.Date), nullable=True),
    )


def downgrade():
    op.drop_column("document_versions", "amended_dates")
