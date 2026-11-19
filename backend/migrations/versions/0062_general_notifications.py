"""Update notices beyond “a cited law changed”: a country's briefing has a new edition, an admission
item an enterprise is preparing was changed by the administrator. Such a notice carries its own
title, text and the page it opens; ``dedupe_key`` keeps one notice per user per event.
``admission_projects.entry_seen_hash`` records the state of the item the project last notified about.
``document_versions.check_note``: the result of the recognition check of a scanned law."""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    for col in ("impact_id", "task_id", "change_id"):
        op.alter_column("user_notifications", col, nullable=True)
    op.add_column("user_notifications", sa.Column("kind", sa.String(24), nullable=False, server_default="source_change"))
    op.add_column("user_notifications", sa.Column("title", sa.Text(), nullable=False, server_default=""))
    op.add_column("user_notifications", sa.Column("body", sa.Text(), nullable=False, server_default=""))
    op.add_column("user_notifications", sa.Column("link", sa.String(512), nullable=False, server_default=""))
    op.add_column("user_notifications", sa.Column("dedupe_key", sa.String(200), nullable=True))
    op.create_index("uq_notification_user_key", "user_notifications", ["user_id", "dedupe_key"], unique=True)
    op.add_column("admission_projects", sa.Column("entry_seen_hash", sa.String(64), nullable=True))
    # 扫描件识别质量检查的结论：通过的记为已核对；没通过的写明哪一项没过
    op.add_column("document_versions", sa.Column("check_note", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("document_versions", "check_note")
    op.drop_column("admission_projects", "entry_seen_hash")
    op.drop_index("uq_notification_user_key", table_name="user_notifications")
    for col in ("dedupe_key", "link", "body", "title", "kind"):
        op.drop_column("user_notifications", col)
