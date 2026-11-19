"""Grant runtime role cyshuhe_app permissions on opinion digest and non-audit business tables."""

from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """DO $$ BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'cyshuhe_app') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON
                    opinion_digest_configs,
                    opinion_digest_runs,
                    legal_term_definitions,
                    change_advice
                TO cyshuhe_app;
            END IF;
        END $$;"""
    )


def downgrade():
    pass
