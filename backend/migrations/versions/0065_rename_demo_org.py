"""The demonstration enterprise gets a fictitious company full name (users see it in the account
list, project titles and generated files): “演示车企” → “启衡智行汽车科技（上海）有限公司”. ``is_demo`` stays; accounts untouched."""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE organizations SET name = '启衡智行汽车科技（上海）有限公司' WHERE name = '演示车企'")


def downgrade():
    op.execute("UPDATE organizations SET name = '演示车企' WHERE name = '启衡智行汽车科技（上海）有限公司' AND is_demo")
