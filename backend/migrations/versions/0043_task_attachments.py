"""Documents a user puts into an assessment conversation, and the review made of each."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def upgrade():
    op.create_table(
        "task_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("readable", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("task_id", "sha256", name="uq_task_attachment_hash"),
    )
    op.create_table(
        "attachment_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "attachment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("task_attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("country_code", sa.String(8), nullable=False),
        sa.Column("blocks", postgresql.JSONB, nullable=False),
        sa.Column("summary", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for table in ("task_attachments", "attachment_reviews"):
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
        op.execute(f"CREATE POLICY tenant_access ON {table} USING ({mine}) WITH CHECK ({mine})")
    op.create_index("ix_task_attachments_task_id", "task_attachments", ["task_id"])
    op.create_index("ix_attachment_reviews_attachment_id", "attachment_reviews", ["attachment_id"])
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON task_attachments, attachment_reviews TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("attachment_reviews")
    op.drop_table("task_attachments")
