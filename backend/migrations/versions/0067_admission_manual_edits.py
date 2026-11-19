"""Admission items are base data every enterprise uses; the administrator corrects what extraction
got wrong. ``admission_entries.edited`` lists the fields an administrator changed by hand
(name_zh, authority_zh, summary_zh, materials_plan) and ``admission_steps.edited`` marks a step
changed by hand: the rebuild commands leave those alone. ``admission_drafts.forms`` keeps the
official forms as they were filled when the material was generated, so every version can be read
and downloaded again (requirement 2.6: keep the filed material)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admission_entries", sa.Column("edited", JSONB(), nullable=False, server_default="[]"))
    op.add_column("admission_steps", sa.Column("edited", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("admission_drafts", sa.Column("forms", JSONB(), nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("admission_drafts", "forms")
    op.drop_column("admission_steps", "edited")
    op.drop_column("admission_entries", "edited")
