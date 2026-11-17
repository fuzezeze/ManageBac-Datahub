"""Analytics views added after the initial roster and attendance summaries.

Imported by migrations so a new database and an upgraded database install the same SQL.
"""

REPORT_VIEWS = [
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_report_student_attendance AS
    SELECT a.school_id, a.business_date, a.attendance_type, a.period, a.standard_status,
           a.category, a.note, s.student_id, s.first_name, s.last_name, s.grade AS student_grade,
           c.name AS class_name, c.grade AS class_grade
    FROM hub_core.attendance_records a
    JOIN hub_core.students s
      ON s.school_id = a.school_id AND s.mb_id = a.student_mb_id
    LEFT JOIN hub_core.classes c
      ON c.school_id = a.school_id AND c.mb_id = a.class_mb_id
    """,
    """
    CREATE OR REPLACE SQL SECURITY DEFINER VIEW hub_analytics.v_report_teacher_assignments AS
    SELECT tc.school_id, tc.member_status, te.first_name, te.last_name, te.identifier,
           c.grade, c.subject_name, c.name AS course_name, c.program, tc.role,
           (
             SELECT yg.name
             FROM hub_core.year_groups yg
             WHERE yg.school_id = c.school_id AND yg.grade = c.grade
             ORDER BY yg.mb_id
             LIMIT 1
           ) AS class_group
    FROM hub_core.teacher_classes tc
    JOIN hub_core.teachers te
      ON te.school_id = tc.school_id AND te.mb_id = tc.teacher_mb_id
    JOIN hub_core.classes c
      ON c.school_id = tc.school_id AND c.mb_id = tc.class_mb_id
    """,
]

REPORT_VIEW_NAMES = (
    "v_report_student_attendance",
    "v_report_teacher_assignments",
)
