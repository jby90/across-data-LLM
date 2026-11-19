"""Each admission item knows how each material the law lists is prepared (``materials_plan``):
written into the generated application from information the enterprise gives once, an official
form, the application itself, or an original the enterprise brings to the authority (listed only).
The platform no longer takes the enterprise's own documents: earlier uploads stay in
``admission_files`` but are not shown."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "admission_entries",
        sa.Column("materials_plan", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade():
    op.drop_column("admission_entries", "materials_plan")
