"""Notices that are not about an assessment report (a briefing's new edition, a changed admission
item) have no task: the tenant guard on ``user_notifications`` checks such a notice's recipient
belongs to the notice's organization instead of matching it to a task and an impact."""

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION enforce_task_tenant() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE parent_org uuid; parent_task uuid; related_change uuid;
        BEGIN
            -- 不挂评估报告的提醒（简报出新版、准入事项有变化）：只核收件人属于这家企业
            IF TG_TABLE_NAME='user_notifications' AND NEW.task_id IS NULL THEN
                IF NEW.kind = 'source_change' THEN RAISE EXCEPTION 'source change notice needs its task'; END IF;
                IF NOT EXISTS(SELECT FROM public.users WHERE id=NEW.user_id AND org_id=NEW.org_id) THEN
                    RAISE EXCEPTION 'notification recipient does not match organization'; END IF;
                RETURN NEW;
            END IF;
            SELECT org_id INTO parent_org FROM public.analysis_tasks WHERE id=NEW.task_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'analysis task unavailable'; END IF;
            IF NEW.org_id IS NOT NULL AND NEW.org_id IS DISTINCT FROM parent_org THEN
                RAISE EXCEPTION 'child organization does not match task'; END IF;
            NEW.org_id=parent_org;
            IF TG_TABLE_NAME='update_impacts' THEN
                SELECT task_id INTO parent_task FROM public.report_versions WHERE id=NEW.report_version_id;
                IF NOT FOUND OR parent_task IS DISTINCT FROM NEW.task_id THEN
                    RAISE EXCEPTION 'report does not match impact task'; END IF;
            ELSIF TG_TABLE_NAME='user_notifications' THEN
                SELECT task_id,change_id INTO parent_task,related_change FROM public.update_impacts WHERE id=NEW.impact_id;
                IF NOT FOUND OR parent_task IS DISTINCT FROM NEW.task_id OR related_change IS DISTINCT FROM NEW.change_id THEN
                    RAISE EXCEPTION 'notification does not match impact'; END IF;
                IF NOT EXISTS(SELECT FROM public.users WHERE id=NEW.user_id AND org_id=parent_org) THEN
                    RAISE EXCEPTION 'notification recipient does not match task organization'; END IF;
            END IF;
            RETURN NEW;
        END $$;""")


def downgrade():
    op.execute("""CREATE OR REPLACE FUNCTION enforce_task_tenant() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE parent_org uuid; parent_task uuid; related_change uuid;
        BEGIN
            SELECT org_id INTO parent_org FROM public.analysis_tasks WHERE id=NEW.task_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'analysis task unavailable'; END IF;
            IF NEW.org_id IS NOT NULL AND NEW.org_id IS DISTINCT FROM parent_org THEN
                RAISE EXCEPTION 'child organization does not match task'; END IF;
            NEW.org_id=parent_org;
            IF TG_TABLE_NAME='update_impacts' THEN
                SELECT task_id INTO parent_task FROM public.report_versions WHERE id=NEW.report_version_id;
                IF NOT FOUND OR parent_task IS DISTINCT FROM NEW.task_id THEN
                    RAISE EXCEPTION 'report does not match impact task'; END IF;
            ELSIF TG_TABLE_NAME='user_notifications' THEN
                SELECT task_id,change_id INTO parent_task,related_change FROM public.update_impacts WHERE id=NEW.impact_id;
                IF NOT FOUND OR parent_task IS DISTINCT FROM NEW.task_id OR related_change IS DISTINCT FROM NEW.change_id THEN
                    RAISE EXCEPTION 'notification does not match impact'; END IF;
                IF NOT EXISTS(SELECT FROM public.users WHERE id=NEW.user_id AND org_id=parent_org) THEN
                    RAISE EXCEPTION 'notification recipient does not match task organization'; END IF;
            END IF;
            RETURN NEW;
        END $$;""")
