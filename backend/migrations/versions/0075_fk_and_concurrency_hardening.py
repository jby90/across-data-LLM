"""Harden foreign-key delete cascades on notifications/impacts and add concurrency unique constraints."""

import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Foreign keys on user_notifications & update_impacts (avoiding SET NULL trigger trap on source_change notices)
    op.drop_constraint("user_notifications_impact_id_fkey", "user_notifications", type_="foreignkey")
    op.create_foreign_key(
        "user_notifications_impact_id_fkey",
        "user_notifications",
        "update_impacts",
        ["impact_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("user_notifications_task_id_fkey", "user_notifications", type_="foreignkey")
    op.create_foreign_key(
        "user_notifications_task_id_fkey",
        "user_notifications",
        "analysis_tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("update_impacts_task_id_fkey", "update_impacts", type_="foreignkey")
    op.create_foreign_key(
        "update_impacts_task_id_fkey",
        "update_impacts",
        "analysis_tasks",
        ["task_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("update_impacts_report_version_id_fkey", "update_impacts", type_="foreignkey")
    op.create_foreign_key(
        "update_impacts_report_version_id_fkey",
        "update_impacts",
        "report_versions",
        ["report_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("update_impacts_resolved_report_id_fkey", "update_impacts", type_="foreignkey")
    op.create_foreign_key(
        "update_impacts_resolved_report_id_fkey",
        "update_impacts",
        "report_versions",
        ["resolved_report_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. Deduplicate & enforce organization-level uniqueness on user_subscriptions
    op.execute(
        """DELETE FROM user_subscriptions a
           USING user_subscriptions b
           WHERE a.org_id = b.org_id
             AND (a.updated_at < b.updated_at OR (a.updated_at = b.updated_at AND a.id::text < b.id::text));"""
    )
    op.drop_constraint("user_subscriptions_user_id_key", "user_subscriptions", type_="unique")
    op.create_unique_constraint("uq_user_subscriptions_org", "user_subscriptions", ["org_id"])

    # 3. Deduplicate & enforce uniqueness on document_instances(task_id, code)
    op.execute(
        """DELETE FROM document_instances a
           USING document_instances b
           WHERE a.task_id = b.task_id
             AND a.code = b.code
             AND (a.updated_at < b.updated_at OR (a.updated_at = b.updated_at AND a.id::text < b.id::text));"""
    )
    op.create_unique_constraint("uq_document_instance_task_code", "document_instances", ["task_id", "code"])

    # 4. Normalize & enforce uniqueness on analysis_step_traces(task_id, seq_no)
    op.execute(
        """WITH ranked AS (
               SELECT id, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY seq_no ASC, created_at ASC, id ASC) AS rn
               FROM analysis_step_traces
           )
           UPDATE analysis_step_traces t
           SET seq_no = r.rn
           FROM ranked r
           WHERE t.id = r.id AND t.seq_no <> r.rn;"""
    )
    op.create_unique_constraint("uq_step_trace_task_seq", "analysis_step_traces", ["task_id", "seq_no"])

    # 5. Deduplicate & enforce partial unique index on form_instances(org_id, task_id, form_code) WHERE task_id IS NOT NULL
    op.execute(
        """DELETE FROM form_instances a
           USING form_instances b
           WHERE a.task_id IS NOT NULL
             AND a.org_id = b.org_id
             AND a.task_id = b.task_id
             AND a.form_code = b.form_code
             AND (a.updated_at < b.updated_at OR (a.updated_at = b.updated_at AND a.id::text < b.id::text));"""
    )
    op.create_index(
        "uq_form_instance_task_code",
        "form_instances",
        ["org_id", "task_id", "form_code"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_form_instance_task_code", table_name="form_instances")
    op.drop_constraint("uq_step_trace_task_seq", "analysis_step_traces", type_="unique")
    op.drop_constraint("uq_document_instance_task_code", "document_instances", type_="unique")
    op.drop_constraint("uq_user_subscriptions_org", "user_subscriptions", type_="unique")
    op.create_unique_constraint("user_subscriptions_user_id_key", "user_subscriptions", ["user_id"])

    op.drop_constraint("update_impacts_resolved_report_id_fkey", "update_impacts", type_="foreignkey")
    op.create_foreign_key(
        "update_impacts_resolved_report_id_fkey",
        "update_impacts",
        "report_versions",
        ["resolved_report_id"],
        ["id"],
    )
    op.drop_constraint("update_impacts_report_version_id_fkey", "update_impacts", type_="foreignkey")
    op.create_foreign_key(
        "update_impacts_report_version_id_fkey",
        "update_impacts",
        "report_versions",
        ["report_version_id"],
        ["id"],
    )
    op.drop_constraint("update_impacts_task_id_fkey", "update_impacts", type_="foreignkey")
    op.create_foreign_key(
        "update_impacts_task_id_fkey",
        "update_impacts",
        "analysis_tasks",
        ["task_id"],
        ["id"],
    )
    op.drop_constraint("user_notifications_task_id_fkey", "user_notifications", type_="foreignkey")
    op.create_foreign_key(
        "user_notifications_task_id_fkey",
        "user_notifications",
        "analysis_tasks",
        ["task_id"],
        ["id"],
    )
    op.drop_constraint("user_notifications_impact_id_fkey", "user_notifications", type_="foreignkey")
    op.create_foreign_key(
        "user_notifications_impact_id_fkey",
        "user_notifications",
        "update_impacts",
        ["impact_id"],
        ["id"],
    )
