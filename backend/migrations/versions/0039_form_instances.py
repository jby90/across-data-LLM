"""Working copies of official forms (answers per organisation), tenant-isolated."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def upgrade():
    op.create_table(
        "form_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("form_code", sa.String(64), nullable=False),
        sa.Column("form_version", sa.String(32), nullable=False),
        sa.Column("answers", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_form_instances_org_id", "form_instances", ["org_id"])
    op.create_index("ix_form_instances_task_id", "form_instances", ["task_id"])
    op.execute("ALTER TABLE form_instances ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE form_instances FORCE ROW LEVEL SECURITY")
    mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
    op.execute(f"CREATE POLICY tenant_access ON form_instances USING ({mine}) WITH CHECK ({mine})")
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON form_instances TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("form_instances")
