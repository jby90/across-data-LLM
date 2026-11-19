"""Chinese title of a regulation, from the source catalogue: citations show it next to the original."""

import sqlalchemy as sa
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("title_zh", sa.String(500), nullable=True))


def downgrade():
    op.drop_column("documents", "title_zh")
