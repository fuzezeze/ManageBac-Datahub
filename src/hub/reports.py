"""Analytics reports. Add a ReportSpec and register it in REPORTS to publish another one.

Reports read hub_analytics views. A platform admin sees every report. Any other
signed-in role sees only the report_grants rows an admin assigned.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from hub.config import get_settings
from hub.models import DatasetSyncState, ReportGrant
from hub.timeutil import utcnow


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
    category: str
    sort: int
    datasets: tuple[str, ...]
    filters: tuple[FilterSpec, ...]
    columns: tuple[ColumnSpec, ...]
    query: Callable[[Session, dict], list[dict]]


def _public(spec: ReportSpec, freshness: dict | None = None) -> dict:
    payload = {
        "key": spec.key,
        "title": spec.title,
        "description": spec.description,
        "category": spec.category,
        "sort": spec.sort,
        "datasets": list(spec.datasets),
        "filters": [{"key": item.key, "label": item.label, "kind": item.kind} for item in spec.filters],
        "columns": [{"key": item.key, "label": item.label} for item in spec.columns],
    }
    if freshness is not None:
        payload["freshness"] = freshness
    return payload


def list_reports() -> list[dict]:
    return [_public(spec) for spec in _sorted(REPORTS.values())]


def granted_keys(session: Session, admin_id) -> set[str]:
    rows = session.scalars(select(ReportGrant.report_key).where(ReportGrant.admin_id == admin_id)).all()
    return {key for key in rows if key in REPORTS}


def visible_reports(session: Session, role: str, admin_id) -> list[dict]:
    if role == "platform_admin":
        specs = _sorted(REPORTS.values())
    else:
        allowed = granted_keys(session, admin_id)
        specs = _sorted(spec for spec in REPORTS.values() if spec.key in allowed)
    stamps = _sync_stamps(session)
    return [_public(spec, _freshness(spec, stamps)) for spec in specs]


def can_run(session: Session, role: str, admin_id, key: str) -> str:
    if key not in REPORTS:
        return "missing"
    if role == "platform_admin" or key in granted_keys(session, admin_id):
        return "ok"
    return "forbidden"


def set_report_grants(session: Session, admin_id, actor_id, keys: object) -> list[str]:
    cleaned = _normalize_keys(keys)
    session.execute(delete(ReportGrant).where(ReportGrant.admin_id == admin_id))
    now = utcnow()
    for key in cleaned:
        session.add(ReportGrant(admin_id=admin_id, report_key=key, granted_by=actor_id, granted_at=now))
    return ordered_keys(cleaned)


def ordered_keys(keys) -> list[str]:
    chosen = set(keys)
    return [spec.key for spec in _sorted(REPORTS.values()) if spec.key in chosen]


def run_report(session: Session, key: str, params: dict) -> dict:
    spec = REPORTS.get(key)
    if spec is None:
        raise KeyError(key)
    rows = spec.query(session, params)
    stamps = _sync_stamps(session)
    return {
        "report": _public(spec, _freshness(spec, stamps)),
        "data": rows,
        "meta": {"returned_count": len(rows), "live": False},
    }


def _sorted(specs) -> list[ReportSpec]:
    return sorted(specs, key=lambda spec: (spec.sort, spec.title))


def _normalize_keys(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ReportError("reports must be a list of report keys")
    cleaned: list[str] = []
    for item in value:
        key = str(item or "").strip()
        if key not in REPORTS:
            raise ReportError(f"Unknown report: {key}")
        if key not in cleaned:
            cleaned.append(key)
    return cleaned


def _sync_stamps(session: Session) -> dict[str, datetime | None]:
    rows = session.scalars(select(DatasetSyncState).where(DatasetSyncState.school_id == get_settings().school_id)).all()
    return {row.dataset: row.last_success_at for row in rows}


def _freshness(spec: ReportSpec, stamps: dict[str, datetime | None]) -> dict:
    datasets = []
    times: list[datetime] = []
    complete = True
    for name in spec.datasets:
        stamp = stamps.get(name)
        if stamp is None:
            complete = False
        else:
            times.append(stamp)
        datasets.append({"dataset": name, "last_success_at": None if stamp is None else stamp.isoformat()})
    as_of = min(times).isoformat() if complete and times else None
    return {"as_of": as_of, "datasets": datasets}


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


def _name(first: str | None, last: str | None) -> str:
    return f"{first or ''} {last or ''}".strip()


def _roster(session: Session, params: dict) -> list[dict]:
    student = _text(params, "student")
    year_group = _text(params, "year_group")
    query = _text(params, "q")
    rows = session.execute(
        text(
            """
            SELECT mb_id, student_id, first_name, last_name, standard_status, year_group_name
            FROM hub_analytics.v_student_roster
            WHERE school_id = :school
              AND (
                    :student IS NULL
                    OR student_id LIKE :student ESCAPE '\\\\'
                    OR first_name LIKE :student ESCAPE '\\\\'
                    OR last_name LIKE :student ESCAPE '\\\\'
                    OR CONCAT(first_name, ' ', last_name) LIKE :student ESCAPE '\\\\'
                  )
              AND (:year_group IS NULL OR year_group_name LIKE :year_group ESCAPE '\\\\')
              AND (
                    :q IS NULL
                    OR student_id LIKE :q ESCAPE '\\\\'
                    OR first_name LIKE :q ESCAPE '\\\\'
                    OR last_name LIKE :q ESCAPE '\\\\'
                    OR year_group_name LIKE :q ESCAPE '\\\\'
                    OR standard_status LIKE :q ESCAPE '\\\\'
                  )
            ORDER BY last_name, first_name, mb_id
            LIMIT 500
            """
        ),
        {
            "school": get_settings().school_id,
            "student": _like(student),
            "year_group": _like(year_group),
            "q": _like(query),
        },
    ).all()
    return [
        {
            "student_name": _name(row.first_name, row.last_name),
            "student_id": row.student_id,
            "year_group_name": row.year_group_name,
            "status": row.standard_status,
        }
        for row in rows
    ]


def _daily(session: Session, params: dict) -> list[dict]:
    start = _day(params, "date_from")
    end = _day(params, "date_to")
    if start and end and end < start:
        raise ReportError("date_to is before date_from")
    rows = session.execute(
        text(
            """
            SELECT business_date, attendance_type, standard_status, SUM(record_count) AS record_count
            FROM hub_analytics.v_attendance_daily_summary
            WHERE school_id = :school
              AND (:date_from IS NULL OR business_date >= :date_from)
              AND (:date_to IS NULL OR business_date <= :date_to)
            GROUP BY business_date, attendance_type, standard_status
            ORDER BY business_date, attendance_type, standard_status
            LIMIT 500
            """
        ),
        {"school": get_settings().school_id, "date_from": start, "date_to": end},
    ).all()
    return [
        {
            "business_date": str(row.business_date),
            "attendance_type": row.attendance_type,
            "status": row.standard_status,
            "record_count": int(row.record_count),
        }
        for row in rows
    ]


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
            SELECT business_date, attendance_type, period, standard_status, category, note,
                   student_id, first_name, last_name, student_grade, class_name, class_grade
            FROM hub_analytics.v_report_student_attendance
            WHERE school_id = :school
              AND (:date_from IS NULL OR business_date >= :date_from)
              AND (:date_to IS NULL OR business_date <= :date_to)
              AND (:grade IS NULL OR student_grade = :grade OR class_grade = :grade)
              AND (:class_name IS NULL OR class_name LIKE :class_name ESCAPE '\\\\')
              AND (
                    :student IS NULL
                    OR student_id LIKE :student ESCAPE '\\\\'
                    OR first_name LIKE :student ESCAPE '\\\\'
                    OR last_name LIKE :student ESCAPE '\\\\'
                    OR CONCAT(first_name, ' ', last_name) LIKE :student ESCAPE '\\\\'
                  )
              AND (
                    :q IS NULL
                    OR student_id LIKE :q ESCAPE '\\\\'
                    OR first_name LIKE :q ESCAPE '\\\\'
                    OR last_name LIKE :q ESCAPE '\\\\'
                    OR student_grade LIKE :q ESCAPE '\\\\'
                    OR class_name LIKE :q ESCAPE '\\\\'
                    OR class_grade LIKE :q ESCAPE '\\\\'
                    OR note LIKE :q ESCAPE '\\\\'
                    OR standard_status LIKE :q ESCAPE '\\\\'
                    OR attendance_type LIKE :q ESCAPE '\\\\'
                  )
            ORDER BY business_date DESC, last_name, first_name, attendance_type, period
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
    return [
        {
            "business_date": str(row.business_date),
            "attendance_type": row.attendance_type,
            "period": row.period,
            "grade": row.class_grade or row.student_grade,
            "class_name": row.class_name,
            "student_id": row.student_id,
            "student_name": _name(row.first_name, row.last_name),
            "status": row.standard_status,
            "category": row.category,
            "note": row.note,
        }
        for row in rows
    ]


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
            SELECT first_name, last_name, identifier, grade, subject_name, course_name, program, role, class_group
            FROM hub_analytics.v_report_teacher_assignments
            WHERE school_id = :school
              AND member_status = 'active'
              AND (
                    :teacher IS NULL
                    OR identifier LIKE :teacher ESCAPE '\\\\'
                    OR first_name LIKE :teacher ESCAPE '\\\\'
                    OR last_name LIKE :teacher ESCAPE '\\\\'
                    OR CONCAT(first_name, ' ', last_name) LIKE :teacher ESCAPE '\\\\'
                  )
              AND (:grade IS NULL OR grade = :grade)
              AND (:subject IS NULL OR subject_name LIKE :subject ESCAPE '\\\\')
              AND (:course IS NULL OR course_name LIKE :course ESCAPE '\\\\')
              AND (
                    :class_name IS NULL
                    OR grade LIKE :class_name ESCAPE '\\\\'
                    OR class_group LIKE :class_name ESCAPE '\\\\'
                  )
              AND (
                    :q IS NULL
                    OR first_name LIKE :q ESCAPE '\\\\'
                    OR last_name LIKE :q ESCAPE '\\\\'
                    OR identifier LIKE :q ESCAPE '\\\\'
                    OR grade LIKE :q ESCAPE '\\\\'
                    OR subject_name LIKE :q ESCAPE '\\\\'
                    OR course_name LIKE :q ESCAPE '\\\\'
                    OR program LIKE :q ESCAPE '\\\\'
                    OR role LIKE :q ESCAPE '\\\\'
                  )
            ORDER BY last_name, first_name, grade, subject_name, course_name
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
            "teacher": _name(row.first_name, row.last_name),
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
            key="student_roster",
            title="Student roster",
            description="Each student once, with year group and status.",
            category="Students",
            sort=10,
            datasets=("students", "year_groups"),
            filters=(
                FilterSpec("student", "Student", "text"),
                FilterSpec("year_group", "Year group", "text"),
                FilterSpec("q", "Search", "text"),
            ),
            columns=(
                ColumnSpec("student_name", "Student"),
                ColumnSpec("student_id", "Student ID"),
                ColumnSpec("year_group_name", "Year group"),
                ColumnSpec("status", "Status"),
            ),
            query=_roster,
        ),
        ReportSpec(
            key="attendance_daily",
            title="Attendance by day",
            description="Record counts by date, type, and status. Attendance rate is not published.",
            category="Attendance",
            sort=20,
            datasets=("attendance_class", "attendance_homeroom"),
            filters=(
                FilterSpec("date_from", "From", "date"),
                FilterSpec("date_to", "To", "date"),
            ),
            columns=(
                ColumnSpec("business_date", "Date"),
                ColumnSpec("attendance_type", "Type"),
                ColumnSpec("status", "Status"),
                ColumnSpec("record_count", "Records"),
            ),
            query=_daily,
        ),
        ReportSpec(
            key="student_attendance",
            title="Student attendance",
            description="Attendance rows, including the note written on the homeroom or class record.",
            category="Attendance",
            sort=30,
            datasets=("students", "classes", "attendance_class", "attendance_homeroom"),
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
            description="Teaching rows. Course is the ManageBac class. Class is the year group for that grade.",
            category="Teaching",
            sort=40,
            datasets=("teachers", "classes", "teacher_classes", "year_groups"),
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
