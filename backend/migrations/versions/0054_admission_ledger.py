"""Admission ledger per requirement 2.6: review questions from the authority and the filed
documents of each handling project; steps marked as from the law, generic or the enterprise's own."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"
UUID = postgresql.UUID(as_uuid=True)


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.add_column("admission_progress", sa.Column("kind", sa.String(16), nullable=False, server_default="custom"))
    op.execute("UPDATE admission_progress SET kind='law' WHERE step_id IS NOT NULL")
    op.create_table(
        "admission_issues",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, nullable=False, index=True),
        sa.Column("project_id", UUID, sa.ForeignKey("admission_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("progress_id", UUID, sa.ForeignKey("admission_progress.id", ondelete="SET NULL"), nullable=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False, server_default=""),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("updated_by", UUID, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "admission_files",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, nullable=False, index=True),
        sa.Column("project_id", UUID, sa.ForeignKey("admission_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
    )
    mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
    for table in ("admission_issues", "admission_files"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_access ON {table} USING ({mine}) WITH CHECK ({mine})")
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON admission_issues, admission_files TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("admission_files")
    op.drop_table("admission_issues")
    op.drop_column("admission_progress", "kind")
