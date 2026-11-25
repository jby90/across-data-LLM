"""Add append-only document_instance_versions snapshots with tenant RLS for rollback."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def upgrade():
    op.create_table(
        "document_instance_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("blocks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("edit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("instance_id", "version_no", name="uq_doc_instance_version_no"),
    )
    op.create_index(
        "ix_document_instance_versions_instance_id",
        "document_instance_versions",
        ["instance_id"],
    )

    op.execute("ALTER TABLE document_instance_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_instance_versions FORCE ROW LEVEL SECURITY")
    via = (
        f"{ADMIN} OR ({SCOPE}='tenant' AND EXISTS(SELECT FROM document_instances i "
        f"WHERE i.id=document_instance_versions.instance_id AND i.org_id={ORG}))"
    )
    op.execute(
        f"CREATE POLICY tenant_access ON document_instance_versions "
        f"USING ({via}) WITH CHECK ({via})"
    )

    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'cyshuhe_app') THEN
                GRANT SELECT, INSERT, DELETE ON document_instance_versions TO cyshuhe_app;
            END IF;
        END $$;"""
    )

    op.execute(
        """CREATE OR REPLACE FUNCTION document_instance_versions_append_only()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION '合规文书历史版本快照不可修改';
            END IF;
            IF TG_OP = 'DELETE' AND EXISTS (
                SELECT 1 FROM document_instances WHERE id = OLD.instance_id
            ) THEN
                RAISE EXCEPTION '合规文书历史版本快照不可单独删除';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;"""
    )
    op.execute(
        "CREATE TRIGGER document_instance_versions_no_tamper "
        "BEFORE UPDATE OR DELETE ON document_instance_versions "
        "FOR EACH ROW EXECUTE FUNCTION document_instance_versions_append_only()"
    )

    # Backfill baseline v1 snapshot for any existing saved document instances
    op.execute(
        """INSERT INTO document_instance_versions (
               id, instance_id, version_no, blocks, edit_count,
               note, user_id, user_name, created_at
           )
           SELECT gen_random_uuid(), i.id, 1, i.blocks, i.edit_count,
                  '历史保存版本', i.created_by, '',
                  COALESCE(i.updated_at, i.created_at, now())
           FROM document_instances i
           WHERE NOT EXISTS (
               SELECT 1 FROM document_instance_versions v WHERE v.instance_id = i.id
           );"""
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS document_instance_versions_no_tamper "
        "ON document_instance_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS document_instance_versions_append_only()")
    op.execute("DROP POLICY IF EXISTS tenant_access ON document_instance_versions")
    op.execute("ALTER TABLE document_instance_versions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_instance_versions DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "ix_document_instance_versions_instance_id",
        table_name="document_instance_versions",
    )
    op.drop_table("document_instance_versions")
