"""Timestamps with a default are NOT NULL, as the models declare; tenant RLS on saved documents."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

COLUMNS = (
    ("clause_readings", "created_at"),
    ("document_edits", "at"),
    ("document_instances", "created_at"),
    ("document_instances", "updated_at"),
    ("document_templates", "created_at"),
    ("opinion_items", "fetched_at"),
    ("standard_entries", "fetched_at"),
    ("translation_chunk_cache", "created_at"),
)


SCOPE = "current_setting('app.scope', true)"
ORG = "NULLIF(current_setting('app.org_id', true),'')::uuid"
ADMIN = f"{SCOPE} IN ('platform','reviewer','system')"


def upgrade():
    # A saved document belongs to the organisation of its assessment, exactly like the task itself;
    # its edit log has no organisation of its own and follows the document it belongs to.
    for table in ("document_instances", "document_edits"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    mine = f"{ADMIN} OR ({SCOPE}='tenant' AND org_id={ORG})"
    op.execute(
        f"CREATE POLICY tenant_access ON document_instances USING ({mine}) WITH CHECK ({mine})"
    )
    via = (
        f"{ADMIN} OR ({SCOPE}='tenant' AND EXISTS(SELECT FROM document_instances i "
        f"WHERE i.id=document_edits.instance_id AND i.org_id={ORG}))"
    )
    op.execute(f"CREATE POLICY tenant_access ON document_edits USING ({via}) WITH CHECK ({via})")
    for table, column in COLUMNS:
        op.execute(f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL")
        op.alter_column(
            table, column, existing_type=sa.DateTime(timezone=True), nullable=False
        )


def downgrade():
    for table in ("document_edits", "document_instances"):
        op.execute(f"DROP POLICY IF EXISTS tenant_access ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table, column in COLUMNS:
        op.alter_column(
            table, column, existing_type=sa.DateTime(timezone=True), nullable=True
        )
