"""Store attendance notes on the published columns and add behavior notes.

Revision 0001 creates tables from the current models, so a brand new database
already has behavior_notes and the attendance note grant. This revision updates
databases created before those existed.
"""

from alembic import op
from sqlalchemy import text

from hub.models import BehaviorNote

revision = "0003_notes"
down_revision = "0002_student_exit_dates"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    rows = op.get_bind().execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'hub_core'
              AND table_name = 'behavior_notes'
            """
        )
    )
    return {row[0] for row in rows}


def upgrade() -> None:
    bind = op.get_bind()
    if "behavior_notes" not in _tables():
        BehaviorNote.__table__.create(bind)
    bind.execute(
        text(
            """
            CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_attendance_detail AS
            SELECT id, school_id, attendance_type, student_mb_id, business_date, class_mb_id,
                   year_group_mb_id, period, source_status, standard_status, category, note
            FROM hub_core.attendance_records
            """
        )
    )
    bind.execute(
        text(
            """
            CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_behavior_notes AS
            SELECT id, school_id, mb_id, student_mb_id, student_id, first_name, last_name, grade,
                   incident_time, behavior_type, notes, next_step, next_step_date, reported_by,
                   homeroom_advisor, author_id, visible_to_parents, visible_to_students
            FROM hub_core.behavior_notes
            """
        )
    )
    bind.execute(text("GRANT SELECT (note) ON hub_core.attendance_records TO 'hub_api'@'%', 'hub_admin'@'%'"))
    bind.execute(text("GRANT SELECT ON hub_core.behavior_notes TO 'hub_api'@'%', 'hub_admin'@'%'"))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("DROP VIEW IF EXISTS hub_analytics.v_behavior_notes"))
    bind.execute(
        text(
            """
            CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_attendance_detail AS
            SELECT id, school_id, attendance_type, student_mb_id, business_date, class_mb_id,
                   year_group_mb_id, period, source_status, standard_status, category
            FROM hub_core.attendance_records
            """
        )
    )
    if "behavior_notes" in _tables():
        bind.execute(text("DROP TABLE hub_core.behavior_notes"))
