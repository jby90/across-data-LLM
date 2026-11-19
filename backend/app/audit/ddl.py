"""Database rules that make the audit table append-only. Used by the migration and by the tests,
so the rule that is tested is the rule that is deployed."""

IMMUTABLE_DDL = (
    """CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger AS $$
    BEGIN RAISE EXCEPTION '审计记录不可修改或删除'; END; $$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS audit_logs_no_change ON audit_logs",
    "CREATE TRIGGER audit_logs_no_change BEFORE UPDATE OR DELETE ON audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable()",
    "DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs",
    "CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_immutable()",
)
