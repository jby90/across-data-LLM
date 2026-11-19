"""Platform audit log: who did what, when, from where; append-only (enforced in the database)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
SCOPE = "current_setting('app.scope', true)"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def upgrade():
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_id", UUID, nullable=True),
        sa.Column("actor_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("actor_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("actor_role", sa.String(32), nullable=False, server_default=""),
        sa.Column("org_id", UUID, nullable=True),
        sa.Column("ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("before", postgresql.JSONB, nullable=True),
        sa.Column("after", postgresql.JSONB, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_logs_at", "audit_logs", ["at"])
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_category", "audit_logs", ["category"])
    # 不可删改：任何角色（含库的属主）对该表的 UPDATE/DELETE/TRUNCATE 都被触发器拒绝
    from app.audit.ddl import IMMUTABLE_DDL

    for statement in IMMUTABLE_DDL:
        op.execute(statement)
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")
    # 写入：任何已绑定身份的请求都可追加；读取：只有平台侧身份
    op.execute("CREATE POLICY audit_insert ON audit_logs FOR INSERT WITH CHECK (true)")
    op.execute(f"CREATE POLICY audit_read ON audit_logs FOR SELECT USING ({ADMIN})")
    op.execute(
        """DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT ON audit_logs TO cyshuhe_app;
        END IF; END $$;"""
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_change ON audit_logs")
    op.drop_table("audit_logs")
    op.execute("DROP FUNCTION IF EXISTS audit_logs_immutable()")
