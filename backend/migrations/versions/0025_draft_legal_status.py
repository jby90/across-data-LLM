"""Allow the draft legal status (bills and consultation drafts are not law in force)."""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_versions_legal_status", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_versions_legal_status",
        "document_versions",
        "legal_status IN ('unknown','in_force','amended','repealed','draft')",
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM document_versions WHERE legal_status = 'draft')
        THEN RAISE EXCEPTION 'retain draft status history'; END IF; END $$;""")
    op.drop_constraint("ck_versions_legal_status", "document_versions", type_="check")
    op.create_check_constraint(
        "ck_versions_legal_status",
        "document_versions",
        "legal_status IN ('unknown','in_force','amended','repealed')",
    )
