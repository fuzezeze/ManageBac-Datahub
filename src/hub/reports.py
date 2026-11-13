"""Live analytics reports. Add a ReportSpec and register it in REPORTS to publish another one."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from hub.config import get_settings


class ReportError(ValueError):
    pass


@dataclass(frozen=True)
class FilterSpec:
    key: str
    label: str
    kind: str


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    label: str


@dataclass(frozen=True)
class ReportSpec:
    key: str
    title: str
    description: str
    filters: tuple[FilterSpec, ...]
    columns: tuple[ColumnSpec, ...]
    query: Callable[[Session, dict], list[dict]]


def _public(spec: ReportSpec) -> dict:
    return {
        "key": spec.key,
        "title": spec.title,
        "description": spec.description,
        "filters": [{"key": item.key, "label": item.label, "kind": item.kind} for item in spec.filters],
        "columns": [{"key": item.key, "label": item.label} for item in spec.columns],
    }


def list_reports() -> list[dict]:
    return [_public(spec) for spec in REPORTS.values()]


def run_report(session: Session, key: str, params: dict) -> dict:
    spec = REPORTS.get(key)
    if spec is None:
        raise KeyError(key)
    rows = spec.query(session, params)
    return {"report": _public(spec), "data": rows, "meta": {"returned_count": len(rows), "live": True}}


def _text(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _day(params: dict, key: str) -> date | None:
    value = _text(params, key)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ReportError(f"{key} must be YYYY-MM-DD") from exc


def _like(value: str | None) -> str | None:
    if value is None:
        return None
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _attendance(session: Session, params: dict) -> list[dict]:
    start = _day(params, "date_from")
    end = _day(params, "date_to")
    if start and end and end < start:
        raise ReportError("date_to is before date_from")
    grade = _text(params, "grade")
    class_name = _text(params, "class_name")
    student = _text(params, "student")
    query = _text(params, "q")
    rows = session.execute(
        text(
            """
            SELECT a.business_date, a.attendance_type, a.period, a.standard_status, a.category, a.note,
                   s.student_id, s.first_name, s.last_name, s.grade AS student_grade,
                   c.name AS class_name, c.grade AS class_grade
            FROM hub_core.attendance_records a
            JOIN hub_core.students s
              ON s.school_id = a.school_id AND s.mb_id = a.student_mb_id
            LEFT JOIN hub_core.classes c
              ON c.school_id = a.school_id AND c.mb_id = a.class_mb_id
            WHERE a.school_id = :school
              AND (:date_from IS NULL OR a.business_date >= :date_from)
              AND (:date_to IS NULL OR a.business_date <= :date_to)
              AND (:grade IS NULL OR s.grade = :grade OR c.grade = :grade)
              AND (:class_name IS NULL OR c.name LIKE :class_name ESCAPE '\\\\')
              AND (
                    :student IS NULL
                    OR s.student_id LIKE :student ESCAPE '\\\\'
                    OR s.first_name LIKE :student ESCAPE '\\\\'
                    OR s.last_name LIKE :student ESCAPE '\\\\'
                    OR CONCAT(s.first_name, ' ', s.last_name) LIKE :student ESCAPE '\\\\'
                  )
              AND (
                    :q IS NULL
                    OR s.student_id LIKE :q ESCAPE '\\\\'
                    OR s.first_name LIKE :q ESCAPE '\\\\'
                    OR s.last_name LIKE :q ESCAPE '\\\\'
                    OR s.grade LIKE :q ESCAPE '\\\\'
                    OR c.name LIKE :q ESCAPE '\\\\'
                    OR c.grade LIKE :q ESCAPE '\\\\'
                    OR a.note LIKE :q ESCAPE '\\\\'
                    OR a.standard_status LIKE :q ESCAPE '\\\\'
                    OR a.attendance_type LIKE :q ESCAPE '\\\\'
                  )
            ORDER BY a.business_date DESC, s.last_name, s.first_name, a.attendance_type, a.period
            LIMIT 500
            """
        ),
        {
            "school": get_settings().school_id,
            "date_from": start,
            "date_to": end,
            "grade": grade,
            "class_name": _like(class_name),
            "student": _like(student),
            "q": _like(query),
        },
    ).all()
    data = []
    for row in rows:
        data.append(
            {
                "business_date": str(row.business_date),
                "attendance_type": row.attendance_type,
                "period": row.period,
                "grade": row.class_grade or row.student_grade,
                "class_name": row.class_name,
                "student_id": row.student_id,
                "student_name": f"{row.first_name or ''} {row.last_name or ''}".strip(),
                "status": row.standard_status,
                "category": row.category,
                "note": row.note,
            }
        )
    return data


def _teaching(session: Session, params: dict) -> list[dict]:
    teacher = _text(params, "teacher")
    grade = _text(params, "grade")
    subject = _text(params, "subject")
    course = _text(params, "course")
    class_name = _text(params, "class_name")
    query = _text(params, "q")
    rows = session.execute(
        text(
            """
            SELECT te.first_name, te.last_name, te.identifier,
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
            WHERE tc.school_id = :school
              AND tc.member_status = 'active'
              AND (
                    :teacher IS NULL
                    OR te.identifier LIKE :teacher ESCAPE '\\\\'
                    OR te.first_name LIKE :teacher ESCAPE '\\\\'
                    OR te.last_name LIKE :teacher ESCAPE '\\\\'
                    OR CONCAT(te.first_name, ' ', te.last_name) LIKE :teacher ESCAPE '\\\\'
                  )
              AND (:grade IS NULL OR c.grade = :grade)
              AND (:subject IS NULL OR c.subject_name LIKE :subject ESCAPE '\\\\')
              AND (:course IS NULL OR c.name LIKE :course ESCAPE '\\\\')
              AND (
                    :class_name IS NULL
                    OR c.grade LIKE :class_name ESCAPE '\\\\'
                    OR EXISTS (
                      SELECT 1 FROM hub_core.year_groups yg
                      WHERE yg.school_id = c.school_id AND yg.grade = c.grade
                        AND yg.name LIKE :class_name ESCAPE '\\\\'
                    )
                  )
              AND (
                    :q IS NULL
                    OR te.first_name LIKE :q ESCAPE '\\\\'
                    OR te.last_name LIKE :q ESCAPE '\\\\'
                    OR te.identifier LIKE :q ESCAPE '\\\\'
                    OR c.grade LIKE :q ESCAPE '\\\\'
                    OR c.subject_name LIKE :q ESCAPE '\\\\'
                    OR c.name LIKE :q ESCAPE '\\\\'
                    OR c.program LIKE :q ESCAPE '\\\\'
                    OR tc.role LIKE :q ESCAPE '\\\\'
                  )
            ORDER BY te.last_name, te.first_name, c.grade, c.subject_name, c.name
            LIMIT 500
            """
        ),
        {
            "school": get_settings().school_id,
            "teacher": _like(teacher),
            "grade": grade,
            "subject": _like(subject),
            "course": _like(course),
            "class_name": _like(class_name),
            "q": _like(query),
        },
    ).all()
    return [
        {
            "teacher": f"{row.first_name or ''} {row.last_name or ''}".strip(),
            "identifier": row.identifier,
            "grade": row.grade,
            "subject": row.subject_name,
            "course": row.course_name,
            "class_name": row.class_group or row.grade,
            "program": row.program,
            "role": row.role,
        }
        for row in rows
    ]


REPORTS: dict[str, ReportSpec] = {
    spec.key: spec
    for spec in (
        ReportSpec(
            key="student_attendance",
            title="Student attendance",
            description="Live attendance rows, including the note written on the homeroom or class record.",
            filters=(
                FilterSpec("date_from", "From", "date"),
                FilterSpec("date_to", "To", "date"),
                FilterSpec("grade", "Grade", "text"),
                FilterSpec("class_name", "Class", "text"),
                FilterSpec("student", "Student", "text"),
                FilterSpec("q", "Search", "text"),
            ),
            columns=(
                ColumnSpec("business_date", "Date"),
                ColumnSpec("grade", "Grade"),
                ColumnSpec("class_name", "Class"),
                ColumnSpec("student_name", "Student"),
                ColumnSpec("student_id", "Student ID"),
                ColumnSpec("attendance_type", "Type"),
                ColumnSpec("period", "Period"),
                ColumnSpec("status", "Status"),
                ColumnSpec("category", "Category"),
                ColumnSpec("note", "Note"),
            ),
            query=_attendance,
        ),
        ReportSpec(
            key="teacher_assignments",
            title="Teacher assignments",
            description="Live teaching rows. Course is the ManageBac class. Class is the year group for that grade.",
            filters=(
                FilterSpec("teacher", "Teacher", "text"),
                FilterSpec("grade", "Grade", "text"),
                FilterSpec("subject", "Subject", "text"),
                FilterSpec("course", "Course", "text"),
                FilterSpec("class_name", "Class", "text"),
                FilterSpec("q", "Search", "text"),
            ),
            columns=(
                ColumnSpec("teacher", "Teacher"),
                ColumnSpec("grade", "Grade"),
                ColumnSpec("subject", "Subject"),
                ColumnSpec("course", "Course"),
                ColumnSpec("class_name", "Class"),
                ColumnSpec("program", "Program"),
                ColumnSpec("role", "Role"),
            ),
            query=_teaching,
        ),
    )
}
