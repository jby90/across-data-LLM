"""Which businesses an admission item applies to (autonomous testing, connected-vehicle cloud, remote
diagnostics/OTA, cockpit/biometrics), judged from the item itself; empty = not judged yet."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admission_entries", sa.Column("functions", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade():
    op.drop_column("admission_entries", "functions")
