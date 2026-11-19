"""Admission as an aid: the enterprise picks a country and a business, the platform lists the
admission items that business needs and prepares the material for each. A project records the
business it was prepared for; the platform's checks of the filled material are kept as issues
(kind “check”, with what they point at)."""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admission_projects", sa.Column("function_key", sa.String(32), nullable=False, server_default=""))
    op.add_column("admission_issues", sa.Column("kind", sa.String(16), nullable=False, server_default="manual"))
    op.add_column("admission_issues", sa.Column("target", sa.String(128), nullable=False, server_default=""))


def downgrade():
    op.drop_column("admission_issues", "target")
    op.drop_column("admission_issues", "kind")
    op.drop_column("admission_projects", "function_key")
