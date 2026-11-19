"""Requirement 2.7 four roles and requirement 2.3 subscriptions.

- ``users``: two more roles, ``ops_admin`` (admin side, base data only) and ``viewer`` (an
  enterprise's read-only account); ``can_export`` says whether a viewer may download.
- ``user_subscriptions``: what regulation changes a user wants to hear about (countries, kinds of
  regulation, business areas).
- ``change_advice``: for one regulation change (a revision of a collected law, or a newly collected
  law), the changed clauses side by side and what it means for an enterprise (impact, what to do),
  written once and shared by every notice about it.
The admission filing status lives in ``admission_projects.milestones`` (no new column)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def _ts(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    op.add_column("users", sa.Column("can_export", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.drop_constraint("ck_users_role_org", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_org",
        "users",
        "(role IN ('platform_admin','ops_admin','reviewer') AND org_id IS NULL) OR "
        "(role IN ('member','viewer') AND org_id IS NOT NULL)",
    )
    op.create_table(
        "user_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("countries", JSONB(), nullable=False, server_default="[]"),
        sa.Column("doc_types", JSONB(), nullable=False, server_default="[]"),
        sa.Column("topics", JSONB(), nullable=False, server_default="[]"),
        _ts("created_at"),
        _ts("updated_at"),
    )
    mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
    op.execute("ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_subscriptions FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_access ON user_subscriptions USING ({mine}) WITH CHECK ({mine})")
    op.create_table(
        "change_advice",
        # change:<version_changes.id>（已收录法规的修订）或 doc:<documents.id>（新收录的法规）
        sa.Column("subject", sa.String(80), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("pairs", JSONB(), nullable=False, server_default="[]"),
        sa.Column("impact_zh", sa.Text(), nullable=False, server_default=""),
        sa.Column("actions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON user_subscriptions TO cyshuhe_app;
        GRANT SELECT,INSERT,UPDATE ON change_advice TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.drop_table("change_advice")
    op.drop_table("user_subscriptions")
    # 库里还有运维管理员或查看者账号时，下面重建约束会失败：先在管理端改掉或停用这些账号
    op.drop_constraint("ck_users_role_org", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_org",
        "users",
        "(role IN ('platform_admin','reviewer') AND org_id IS NULL) OR (role = 'member' AND org_id IS NOT NULL)",
    )
    op.drop_column("users", "can_export")
