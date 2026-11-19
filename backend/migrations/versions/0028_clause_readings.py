"""Cross-checked second reading of clauses whose text layer lost glyphs (search and translation)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "clause_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "clause_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clauses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reading_text", sa.Text, nullable=False),
        sa.Column("method", sa.String(32), nullable=False, server_default="dual_route"),
        sa.Column("confirmed_words", sa.Integer, nullable=False, server_default="0"),
        sa.Column("unresolved_spans", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("clause_id", name="uq_clause_readings_clause"),
    )
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON clause_readings TO cyshuhe_app;
        END IF; END $$;""")


def downgrade():
    op.drop_table("clause_readings")
