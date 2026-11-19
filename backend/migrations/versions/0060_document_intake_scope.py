"""A long law may be taken in by its business-relevant sections: ``documents.intake_scope`` records
which parts of which version are in scope (see ``app.ingestion.intake_scope``). Empty means the
whole document."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "documents",
        sa.Column("intake_scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade():
    op.drop_column("documents", "intake_scope")
