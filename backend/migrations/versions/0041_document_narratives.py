"""Argumentative sections of compliance documents (DPIA, LIA, TIA), written once per set of facts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def upgrade():
    op.create_table(
        "document_narratives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("blocks", postgresql.JSONB, nullable=False),
        sa.Column("report", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "code", "inputs_hash", name="uq_document_narrative_inputs"),
    )
    op.create_index("ix_document_narratives_org_id", "document_narratives", ["org_id"])
    op.create_index("ix_document_narratives_task_id", "document_narratives", ["task_id"])
    op.execute("ALTER TABLE document_narratives ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_narratives FORCE ROW LEVEL SECURITY")
    mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
    op.execute(f"CREATE POLICY tenant_access ON document_narratives USING ({mine}) WITH CHECK ({mine})")
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON document_narratives TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("document_narratives")
