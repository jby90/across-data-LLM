"""A re-parse of the same archived file is a new version (parse_revision), not a duplicate."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_versions",
        sa.Column("parse_revision", sa.Integer, nullable=False, server_default="1"),
    )
    op.drop_constraint("uq_versions_doc_hash", "document_versions", type_="unique")
    op.create_unique_constraint(
        "uq_versions_doc_hash_rev",
        "document_versions",
        ["document_id", "content_hash", "parse_revision"],
    )


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM document_versions WHERE parse_revision > 1)
        THEN RAISE EXCEPTION 'retain re-parse revisions'; END IF; END $$;""")
    op.drop_constraint("uq_versions_doc_hash_rev", "document_versions", type_="unique")
    op.create_unique_constraint(
        "uq_versions_doc_hash", "document_versions", ["document_id", "content_hash"]
    )
    op.drop_column("document_versions", "parse_revision")
