"""Create opinion_digest_configs and opinion_digest_runs tables for weekly compliance digest."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "opinion_digest_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("weekday", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("hour_utc", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("lookback_days", sa.Integer(), server_default=sa.text("7"), nullable=False),
        sa.Column("min_level", sa.String(16), server_default=sa.text("'low'"), nullable=False),
        sa.Column("recipients", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("smtp_host", sa.String(255), server_default=sa.text("''"), nullable=False),
        sa.Column("smtp_port", sa.Integer(), server_default=sa.text("587"), nullable=False),
        sa.Column("smtp_user", sa.String(255), server_default=sa.text("''"), nullable=False),
        sa.Column("smtp_from", sa.String(255), server_default=sa.text("''"), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "opinion_digest_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("item_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("items_json", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("html_body", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("delivery_mode", sa.String(32), server_default=sa.text("'portal_only'"), nullable=False),
        sa.Column("recipients", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'sent'"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_opinion_digest_runs_created", "opinion_digest_runs", ["created_at"])


def downgrade():
    op.drop_index("ix_opinion_digest_runs_created", table_name="opinion_digest_runs")
    op.drop_table("opinion_digest_runs")
    op.drop_table("opinion_digest_configs")
