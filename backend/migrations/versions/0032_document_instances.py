"""Generated compliance documents kept as records, with an edit log."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name_zh", sa.String(128), nullable=False),
        sa.Column("template_version", sa.Integer, nullable=False),
        sa.Column("blocks", postgresql.JSONB, nullable=False),
        sa.Column("edit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_instances_task_id", "document_instances", ["task_id"])
    op.create_index("ix_document_instances_org_id", "document_instances", ["org_id"])
    op.create_table(
        "document_edits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("place", sa.String(128), nullable=False),
        sa.Column("before", sa.Text, nullable=False),
        sa.Column("after", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_edits_instance_id", "document_edits", ["instance_id"])
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON document_instances, document_edits TO cyshuhe_app;
        END IF; END $$;""")


def downgrade():
    op.drop_table("document_edits")
    op.drop_table("document_instances")
