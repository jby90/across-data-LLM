"""Isolate ops_admin DB RLS scope from tenant tables and enforce strict document_edits immutability."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None

SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
USER = "NULLIF(current_setting('app.user_id', true),'')::uuid"
EMAIL = "NULLIF(current_setting('app.auth_email', true),'')"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"
PUBLIC_AUDIT = (
    "'documents','document_versions','clauses','clause_translations',"
    "'kg_nodes','kg_edges','kg_country','glossary_terms','source_sites','agent_workflow_configs'"
)


def upgrade():
    # Ensure global workflow baseline audit snapshots stay org_id IS NULL (never stamped with first tenant)
    op.execute(
        f"""CREATE OR REPLACE FUNCTION assign_audit_tenant() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.target_type='organization_redaction' THEN NEW.org_id=NEW.target_id;
            ELSIF NEW.target_type IN ({PUBLIC_AUDIT}) THEN NEW.org_id=NULL;
            ELSIF NEW.org_id IS NULL THEN NEW.org_id={ORG}; END IF;
            RETURN NEW;
        END $$;"""
    )
    op.execute("DROP POLICY IF EXISTS tenant_read ON knowledge_audit_records")
    op.execute(
        f"CREATE POLICY tenant_read ON knowledge_audit_records FOR SELECT USING ("
        f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) "
        f"OR (org_id IS NULL AND target_type IN ({PUBLIC_AUDIT})))"
    )

    # 1. Isolate ops_admin (`app.scope = 'ops'`):
    #    - May read ONLY its own User identity row (`id = app.user_id`);
    #    - May insert/read ONLY public (org_id IS NULL) LLM call audits and public knowledge audit records;
    #    - Strictly zero access to organizations, tenant tasks, redaction terms, or audit_logs.
    op.execute("DROP POLICY IF EXISTS tenant_read ON users")
    op.execute(
        f"CREATE POLICY tenant_read ON users FOR SELECT USING ("
        f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) "
        f"OR ({SCOPE} IN ('identity','ops') AND id={USER}) "
        f"OR ({SCOPE}='auth' AND email={EMAIL}))"
    )

    op.execute("DROP POLICY IF EXISTS tenant_update ON users")
    op.execute(
        f"CREATE POLICY tenant_update ON users FOR UPDATE USING ("
        f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) "
        f"OR ({SCOPE} IN ('identity','ops') AND id={USER})) "
        f"WITH CHECK ("
        f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) "
        f"OR ({SCOPE}='identity' AND id={USER}) "
        f"OR ({SCOPE}='ops' AND id={USER} AND role='ops_admin' AND org_id IS NULL))"
    )

    op.execute("DROP POLICY IF EXISTS tenant_access ON llm_call_audit")
    op.execute(
        f"CREATE POLICY tenant_access ON llm_call_audit USING ("
        f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) OR ({SCOPE}='ops' AND org_id IS NULL)) "
        f"WITH CHECK ({ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) OR ({SCOPE}='ops' AND org_id IS NULL))"
    )

    op.execute("DROP POLICY IF EXISTS tenant_insert ON knowledge_audit_records")
    op.execute(
        f"CREATE POLICY tenant_insert ON knowledge_audit_records FOR INSERT WITH CHECK ("
        f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG}) "
        f"OR ({SCOPE}='ops' AND org_id IS NULL AND target_type IN ({PUBLIC_AUDIT})))"
    )

    # 2. Make document_edits strictly immutable (Redline 8.1):
    #    Add standalone tenant/task/doc trace columns, remove ON DELETE CASCADE from instance_id,
    #    revoke DELETE from cyshuhe_app, and reject ALL UPDATE/DELETE operations unconditionally.
    op.add_column("document_edits", sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("document_edits", sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("document_edits", sa.Column("doc_code", sa.String(length=64), nullable=True))

    op.execute("ALTER TABLE document_edits DISABLE TRIGGER document_edits_no_tamper")
    op.execute(
        """UPDATE document_edits e
           SET org_id = i.org_id, task_id = i.task_id, doc_code = i.code
           FROM document_instances i
           WHERE e.instance_id = i.id AND e.org_id IS NULL;"""
    )
    op.execute("ALTER TABLE document_edits ENABLE TRIGGER document_edits_no_tamper")

    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'f'
                  AND t.relname = 'document_edits'
                  AND a.attname = 'instance_id'
            LOOP
                EXECUTE format('ALTER TABLE document_edits DROP CONSTRAINT %I', r.conname);
            END LOOP;
        END $$;"""
    )
    op.alter_column("document_edits", "instance_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_index("ix_document_edits_org_id", "document_edits", ["org_id"])
    op.create_index("ix_document_edits_task_code", "document_edits", ["task_id", "doc_code"])

    op.execute("DROP POLICY IF EXISTS tenant_access ON document_edits")
    via = (
        f"{ADMIN} OR ({SCOPE}='tenant' AND ("
        f"org_id={ORG} OR EXISTS(SELECT FROM document_instances i "
        f"WHERE i.id=document_edits.instance_id AND i.org_id={ORG})))"
    )
    op.execute(f"CREATE POLICY tenant_access ON document_edits USING ({via}) WITH CHECK ({via})")

    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'cyshuhe_app') THEN
                REVOKE UPDATE, DELETE ON TABLE document_edits FROM cyshuhe_app;
            END IF;
        END $$;"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION document_edits_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '合规文书修订记录属不可变审计存证，严禁修改或删除';
        END;
        $$ LANGUAGE plpgsql;"""
    )
    op.execute("DROP TRIGGER IF EXISTS document_edits_no_tamper ON document_edits")
    op.execute(
        "CREATE TRIGGER document_edits_no_tamper BEFORE UPDATE OR DELETE ON document_edits "
        "FOR EACH ROW EXECUTE FUNCTION document_edits_append_only()"
    )


def downgrade():
    raise RuntimeError("Immutable audit and tenant RLS hardening migration 0078 is non-downgradable")
