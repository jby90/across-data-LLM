"""Knowledge-graph nodes and relations: result of the automatic verification."""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("kg_nodes", "kg_edges"):
        op.add_column(table, sa.Column("verify_state", sa.String(16), nullable=True))
        op.add_column(table, sa.Column("verify_note", sa.Text, nullable=True))
        op.add_column(table, sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for table in ("kg_edges", "kg_nodes"):
        op.drop_column(table, "verified_at")
        op.drop_column(table, "verify_note")
        op.drop_column(table, "verify_state")
