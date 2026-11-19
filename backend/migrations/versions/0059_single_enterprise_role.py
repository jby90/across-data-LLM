"""Accounts come in two kinds: the platform administrator (admin side) and the enterprise user (user
side). Enterprise administrators and members become one enterprise-user role; no account is
deleted. The seeded demo company is marked as such: real companies are named by the full name
given at registration."""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_users_role_org", "users", type_="check")
    op.execute("UPDATE users SET role = 'member' WHERE role = 'org_admin'")
    op.create_check_constraint(
        "ck_users_role_org",
        "users",
        "(role IN ('platform_admin','reviewer') AND org_id IS NULL) OR "
        "(role = 'member' AND org_id IS NOT NULL)",
    )
    op.add_column(
        "organizations",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute("UPDATE organizations SET is_demo = true WHERE name = '演示车企'")


def downgrade():
    # 合并后无法区分原来的管理员与成员：降级只放宽约束，角色保持企业用户
    op.drop_column("organizations", "is_demo")
    op.drop_constraint("ck_users_role_org", "users", type_="check")
    op.create_check_constraint(
        "ck_users_role_org",
        "users",
        "(role IN ('platform_admin','reviewer') AND org_id IS NULL) OR "
        "(role IN ('org_admin','member') AND org_id IS NOT NULL)",
    )
