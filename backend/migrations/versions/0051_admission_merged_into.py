"""Admission entries that describe the same procedure as another entry point to it (merged_into):
the user sees one entry, with the legal bases of all of them."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admission_entries", sa.Column("merged_into", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_admission_entries_merged_into", "admission_entries", ["merged_into"])


def downgrade():
    op.drop_index("ix_admission_entries_merged_into", table_name="admission_entries")
    op.drop_column("admission_entries", "merged_into")
