"""An attachment belongs to the chat message it was sent with (before that it is only in the composer)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("task_attachments", sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True))
    # 已有的文件都是旧流程上传、已出现在对话里的：视为已发送
    op.execute("UPDATE task_attachments SET message_id = gen_random_uuid() WHERE message_id IS NULL")


def downgrade():
    op.drop_column("task_attachments", "message_id")
