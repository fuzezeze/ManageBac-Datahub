"""Initial MySQL databases' tables, views, and grants.

The four databases (hub_raw, hub_core, hub_ops, hub_analytics) and the
application users are created by scripts/init-mysql.sql before this runs.
"""

from alembic import op
from sqlalchemy import text

from hub.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

ATTENDANCE_COLUMNS = """
  id, school_id, attendance_type, student_mb_id, business_date, class_mb_id,
  year_group_mb_id, period, source_status, standard_status, category, note, source_record_id,
  ingested_at, last_seen_at, normalized_updated_at, sync_job_id, class_key, year_group_key
"""

CORE_TABLES = (
    "students",
    "teachers",
    "academic_years",
    "terms",
    "subjects",
    "courses",
    "year_groups",
    "classes",
    "student_year_groups",
    "student_classes",
    "teacher_classes",
    "year_group_advisors",
    "relationship_observations",
    "attendance_revisions",
    "behavior_notes",
)

VIEWS = [
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_student_roster AS
    SELECT s.id, s.school_id, s.mb_id, s.student_id, s.first_name, s.last_name,
           s.standard_status, s.program, s.grade, s.year_group_mb_id,
           yg.name AS year_group_name, s.last_seen_at, s.normalized_updated_at
    FROM hub_core.students s
    LEFT JOIN hub_core.year_groups yg
      ON yg.school_id = s.school_id AND yg.mb_id = s.year_group_mb_id AND yg.source_system = s.source_system
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_class_roster AS
    SELECT c.school_id, c.mb_id AS class_mb_id, c.name AS class_name, c.program, c.grade,
           c.start_term_mb_id, c.end_term_mb_id,
           sc.student_mb_id, st.first_name AS student_first_name, st.last_name AS student_last_name,
           sc.level, sc.member_status AS student_member_status,
           tc.teacher_mb_id, te.first_name AS teacher_first_name, te.last_name AS teacher_last_name,
           tc.show_on_reports, tc.role AS teacher_role, tc.member_status AS teacher_member_status
    FROM hub_core.classes c
    LEFT JOIN hub_core.student_classes sc
      ON sc.school_id = c.school_id AND sc.class_mb_id = c.mb_id AND sc.member_status = 'active'
    LEFT JOIN hub_core.students st
      ON st.school_id = sc.school_id AND st.mb_id = sc.student_mb_id
    LEFT JOIN hub_core.teacher_classes tc
      ON tc.school_id = c.school_id AND tc.class_mb_id = c.mb_id AND tc.member_status = 'active'
    LEFT JOIN hub_core.teachers te
      ON te.school_id = tc.school_id AND te.mb_id = tc.teacher_mb_id
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_year_group_membership AS
    SELECT o.school_id, o.year_group_mb_id, o.student_mb_id, o.member_status, o.role,
           o.observed_from, o.observed_to, 'observed' AS history_basis
    FROM hub_core.relationship_observations o
    WHERE o.relation_type = 'student_year_group'
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_attendance_detail AS
    SELECT id, school_id, attendance_type, student_mb_id, business_date, class_mb_id,
           year_group_mb_id, period, source_status, standard_status, category, note
    FROM hub_core.attendance_records
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_behavior_notes AS
    SELECT id, school_id, mb_id, student_mb_id, student_id, first_name, last_name, grade,
           incident_time, behavior_type, notes, next_step, next_step_date, reported_by,
           homeroom_advisor, author_id, visible_to_parents, visible_to_students
    FROM hub_core.behavior_notes
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_attendance_daily_summary AS
    SELECT school_id, business_date, attendance_type, class_mb_id, year_group_mb_id,
           standard_status, CAST(COUNT(*) AS SIGNED) AS record_count
    FROM hub_core.attendance_records
    GROUP BY school_id, business_date, attendance_type, class_mb_id, year_group_mb_id, standard_status
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_sync_dataset_status AS
    SELECT school_id, dataset, last_attempt_at, last_success_at, last_attempt_status,
           coverage_from, coverage_to, record_count, status, error_summary
    FROM hub_ops.dataset_sync_state
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_api_usage_daily AS
    SELECT client_id, DATE(bucket_start) AS usage_date, route_template,
           request_count, success_count, error_count, record_count, byte_count
    FROM hub_ops.api_usage_buckets
    WHERE bucket_size = 'day'
    """,
]


def _grant(privileges: str, target: str, users: str) -> str:
    return f"GRANT {privileges} ON {target} TO {users}"


def _grants() -> list[str]:
    sync_ops = (
        "sync_schedules",
        "sync_jobs",
        "sync_attempts",
        "sync_checkpoints",
        "dataset_sync_state",
        "coverage_windows",
        "connection_checks",
        "quality_issues",
        "alerts",
    )
    api_read = ("dataset_sync_state", "coverage_windows", "api_clients", "client_permissions", "client_ranges", "client_field_grants")
    auth_read = ("api_clients", "api_credentials", "client_permissions", "client_ranges", "client_field_grants")
    admin_write = (
        "sync_schedules",
        "sync_jobs",
        "sync_attempts",
        "sync_checkpoints",
        "dataset_sync_state",
        "coverage_windows",
        "connection_checks",
        "quality_issues",
        "api_clients",
        "api_credentials",
        "client_permissions",
        "client_ranges",
        "client_field_grants",
        "api_usage_buckets",
        "quota_counters",
        "alerts",
        "admins",
        "admin_sessions",
    )
    statements = [
        "REVOKE ALL PRIVILEGES ON hub_ops.* FROM 'hub_admin'@'%'",
        _grant("SELECT, INSERT, UPDATE, DELETE", "hub_raw.*", "'hub_sync'@'%'"),
        _grant("SELECT, INSERT, UPDATE, DELETE", "hub_core.*", "'hub_sync'@'%'"),
    ]
    for table in admin_write:
        statements.append(_grant("SELECT, INSERT, UPDATE, DELETE", f"hub_ops.{table}", "'hub_admin'@'%'"))
    for table in ("api_request_logs", "admin_audit_logs"):
        statements.append(_grant("SELECT, INSERT, DELETE", f"hub_ops.{table}", "'hub_admin'@'%'"))
    for table in sync_ops:
        statements.append(_grant("SELECT, INSERT, UPDATE, DELETE", f"hub_ops.{table}", "'hub_sync'@'%'"))
    for table in api_read:
        statements.append(_grant("SELECT", f"hub_ops.{table}", "'hub_api'@'%'"))
    statements.append(_grant("SELECT, INSERT", "hub_ops.api_request_logs", "'hub_api'@'%'"))
    statements.append(_grant("SELECT, INSERT", "hub_ops.alerts", "'hub_api'@'%'"))
    statements.append(_grant("SELECT, INSERT, UPDATE", "hub_ops.api_usage_buckets", "'hub_api'@'%'"))
    statements.append(_grant("SELECT, INSERT, UPDATE", "hub_ops.quota_counters", "'hub_api'@'%'"))
    for table in auth_read:
        statements.append(_grant("SELECT", f"hub_ops.{table}", "'hub_auth'@'%'"))
    for user in ("'hub_analytics'@'%'", "'hub_admin'@'%'", "'hub_api'@'%'"):
        statements.append(_grant("SELECT", "hub_analytics.*", user))
    for table in CORE_TABLES:
        statements.append(_grant("SELECT", f"hub_core.{table}", "'hub_api'@'%', 'hub_admin'@'%'"))
    statements.append(
        _grant(f"SELECT ({' '.join(ATTENDANCE_COLUMNS.split())})", "hub_core.attendance_records", "'hub_api'@'%', 'hub_admin'@'%'")
    )
    return statements


def _execute_each(statements: list[str], ignore: set[int] | None = None) -> None:
    bind = op.get_bind()
    for statement in statements:
        try:
            bind.execute(text(statement))
        except Exception as exc:
            code = getattr(getattr(exc, "orig", None), "args", [None])[0]
            if ignore and code in ignore:
                continue
            raise


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    _execute_each(VIEWS)
    grants = _grants()
    _execute_each(grants[:1], ignore={1141, 1147})
    _execute_each(grants[1:])


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "v_student_roster",
        "v_class_roster",
        "v_year_group_membership",
        "v_attendance_detail",
        "v_attendance_daily_summary",
        "v_behavior_notes",
        "v_sync_dataset_status",
        "v_api_usage_daily",
    ):
        bind.execute(text(f"DROP VIEW IF EXISTS hub_analytics.{name}"))
    Base.metadata.drop_all(bind)
