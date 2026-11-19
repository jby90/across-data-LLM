"""Overseas market admission: platform-wide entries and steps, per-organisation projects and ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"
UUID = postgresql.UUID(as_uuid=True)


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade():
    op.create_table(
        "admission_entries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("country_code", sa.String(8), nullable=False),
        sa.Column("name_zh", sa.String(256), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("authority_zh", sa.String(256), nullable=False, server_default=""),
        sa.Column("summary_zh", sa.Text, nullable=False, server_default=""),
        sa.Column("edge_id", UUID, nullable=True),
        sa.Column("evidence_clause_id", UUID, sa.ForeignKey("clauses.id"), nullable=True),
        sa.Column("evidence_quote", sa.Text, nullable=False, server_default=""),
        sa.Column("review_status", sa.String(24), nullable=False, server_default="pending_review"),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_admission_entries_country_code", "admission_entries", ["country_code"])
    op.create_index("ux_admission_entries_edge", "admission_entries", ["edge_id"], unique=True)
    op.create_table(
        "admission_steps",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "entry_id", UUID, sa.ForeignKey("admission_entries.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("title_zh", sa.String(256), nullable=False),
        sa.Column("detail_zh", sa.Text, nullable=False, server_default=""),
        sa.Column("materials", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("evidence_clause_id", UUID, sa.ForeignKey("clauses.id"), nullable=True),
        sa.Column("evidence_quote", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_admission_steps_entry_id", "admission_steps", ["entry_id"])
    op.create_table(
        "admission_projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("entry_id", UUID, sa.ForeignKey("admission_entries.id"), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("applicant", sa.String(256), nullable=False, server_default=""),
        sa.Column("vehicle_model", sa.String(256), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_admission_projects_org_id", "admission_projects", ["org_id"])
    op.create_table(
        "admission_progress",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("admission_projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("step_id", UUID, sa.ForeignKey("admission_steps.id"), nullable=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("title_zh", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="todo"),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("owner", sa.String(128), nullable=False, server_default=""),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("updated_by", UUID, nullable=True),
        _ts("updated_at"),
    )
    op.create_index("ix_admission_progress_project_id", "admission_progress", ["project_id"])
    op.create_table(
        "admission_drafts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID, sa.ForeignKey("admission_projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("org_id", UUID, nullable=False),
        sa.Column("name_zh", sa.String(128), nullable=False),
        sa.Column("blocks", postgresql.JSONB, nullable=False),
        sa.Column("missing", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("created_by", UUID, nullable=True),
        _ts("created_at"),
    )
    op.create_index("ix_admission_drafts_project_id", "admission_drafts", ["project_id"])
    mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
    for table in ("admission_projects", "admission_drafts"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_access ON {table} USING ({mine}) WITH CHECK ({mine})")
    op.execute("ALTER TABLE admission_progress ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE admission_progress FORCE ROW LEVEL SECURITY")
    via = (
        f"{ADMIN} OR ({SCOPE}='tenant' AND EXISTS(SELECT FROM admission_projects p "
        f"WHERE p.id=admission_progress.project_id AND p.org_id={ORG}))"
    )
    op.execute(f"CREATE POLICY tenant_access ON admission_progress USING ({via}) WITH CHECK ({via})")
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON admission_entries, admission_steps, admission_projects,
        admission_progress, admission_drafts TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    for table in (
        "admission_drafts",
        "admission_progress",
        "admission_projects",
        "admission_steps",
        "admission_entries",
    ):
        op.drop_table(table)
