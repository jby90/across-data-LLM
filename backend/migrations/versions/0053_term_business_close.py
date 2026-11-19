"""How close a legal term is to an automotive cross-border data business: the country glossary
lists the close ones first."""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("legal_term_definitions", sa.Column("business_close", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("legal_term_definitions", "business_close")
