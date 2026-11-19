"""Versioned templates for the standard compliance documents."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("name_zh", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("definition", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("code", "version_no", name="uq_document_template_version"),
        sa.CheckConstraint("status IN ('published','retired')", name="ck_document_template_status"),
    )
    op.create_index("ix_document_templates_code", "document_templates", ["code"])
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='cyshuhe_app') THEN
        GRANT SELECT,INSERT,UPDATE,DELETE ON document_templates TO cyshuhe_app; END IF; END $$;""")


def downgrade():
    op.drop_index("ix_document_templates_code", table_name="document_templates")
    op.drop_table("document_templates")
