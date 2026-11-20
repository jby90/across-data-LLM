"""Harden admission/update foreign keys, index current clause_translations, and make document_edits append-only."""

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def _replace_fk(table: str, column: str, ref_table: str, ref_column: str, ondelete: str | None) -> None:
    on_del_sql = f" ON DELETE {ondelete}" if ondelete else ""
    op.execute(
        f"""DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'f'
                  AND t.relname = '{table}'
                  AND a.attname = '{column}'
            LOOP
                EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I', '{table}', r.conname);
            END LOOP;
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I(%I){on_del_sql}',
                '{table}', '{table}_{column}_fkey', '{column}', '{ref_table}', '{ref_column}'
            );
        END $$;"""
    )


def upgrade():
    # 1. Admission & regulatory discovery / site / notification foreign-key cascade alignment
    _replace_fk("admission_entries", "evidence_clause_id", "clauses", "id", "SET NULL")
    _replace_fk("admission_steps", "evidence_clause_id", "clauses", "id", "SET NULL")
    _replace_fk("admission_progress", "step_id", "admission_steps", "id", "SET NULL")
    _replace_fk("regulatory_discoveries", "document_id", "documents", "id", "SET NULL")
    _replace_fk("source_sites", "terms_decided_by", "users", "id", "SET NULL")
    _replace_fk("update_impacts", "org_id", "organizations", "id", "SET NULL")
    _replace_fk("user_notifications", "org_id", "organizations", "id", "CASCADE")
    _replace_fk("user_notifications", "user_id", "users", "id", "CASCADE")
    _replace_fk("user_subscriptions", "org_id", "organizations", "id", "CASCADE")

    # 2. Deduplicate any multi-current clause_translations rows and add partial lookup index
    # (Write paths serialize via SELECT clauses.id ... FOR UPDATE while keeping ambiguous_current defense testable)
    op.execute(
        """WITH ranked AS (
               SELECT id,
                      ROW_NUMBER() OVER (
                          PARTITION BY clause_id
                          ORDER BY quality_check_passed DESC, updated_at DESC, id DESC
                      ) AS rn
               FROM clause_translations
               WHERE is_current = true
           )
           UPDATE clause_translations ct
           SET is_current = false,
               review_status = CASE
                   WHEN ct.review_status IN ('approved', 'machine_translated', 'pending_review') THEN 'superseded'
                   ELSE ct.review_status
               END
           FROM ranked r
           WHERE ct.id = r.id AND r.rn > 1;"""
    )
    op.execute("DROP INDEX IF EXISTS uq_clause_translations_one_current")
    op.create_index(
        "ix_clause_translations_current",
        "clause_translations",
        ["clause_id"],
        unique=False,
        postgresql_where=sa.text("is_current = true"),
    )

    # 3. Protect compliance document edit audit trail (document_edits) against tampering while allowing parent DocumentInstance cascade reset/cleanup
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'cyshuhe_app') THEN
                REVOKE UPDATE ON TABLE document_edits FROM cyshuhe_app;
            END IF;
        END $$;"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION document_edits_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION '合规文书修订记录不可修改';
            END IF;
            IF TG_OP = 'DELETE' AND EXISTS (SELECT 1 FROM document_instances WHERE id = OLD.instance_id) THEN
                RAISE EXCEPTION '合规文书修订记录不可单独删除';
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql;"""
    )
    op.execute("DROP TRIGGER IF EXISTS document_edits_no_tamper ON document_edits")
    op.execute(
        "CREATE TRIGGER document_edits_no_tamper BEFORE UPDATE OR DELETE ON document_edits "
        "FOR EACH ROW EXECUTE FUNCTION document_edits_append_only()"
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS document_edits_no_tamper ON document_edits")
    op.execute("DROP FUNCTION IF EXISTS document_edits_append_only()")
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'cyshuhe_app') THEN
                GRANT UPDATE ON TABLE document_edits TO cyshuhe_app;
            END IF;
        END $$;"""
    )
    op.execute("DROP INDEX IF EXISTS uq_clause_translations_one_current")
    op.execute("DROP INDEX IF EXISTS ix_clause_translations_current")
    _replace_fk("user_subscriptions", "org_id", "organizations", "id", None)
    _replace_fk("user_notifications", "user_id", "users", "id", None)
    _replace_fk("user_notifications", "org_id", "organizations", "id", None)
    _replace_fk("update_impacts", "org_id", "organizations", "id", None)
    _replace_fk("source_sites", "terms_decided_by", "users", "id", None)
    _replace_fk("regulatory_discoveries", "document_id", "documents", "id", None)
    _replace_fk("admission_progress", "step_id", "admission_steps", "id", None)
    _replace_fk("admission_steps", "evidence_clause_id", "clauses", "id", None)
    _replace_fk("admission_entries", "evidence_clause_id", "clauses", "id", None)
