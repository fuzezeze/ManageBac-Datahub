from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


STUDENT_STATUS = {
    "enrolled": "enrolled",
    "withdrawn": "withdrawn",
    "graduated": "graduated",
    "archived": "archived",
    "deleted": "deleted",
}

ATTENDANCE_STATUS = {
    "present": "present",
    "p": "present",
    "absent": "absent",
    "a": "absent",
    "late": "late",
    "l": "late",
    "tardy": "late",
    "excused": "excused",
}


class ShapeError(ValueError):
    """The payload does not match the endpoint contract. Do not publish it."""


@dataclass
class AttendanceDraft:
    attendance_type: str
    student_mb_id: str
    business_date: date
    class_mb_id: str | None
    year_group_mb_id: str | None
    period: str
    source_status: str | None
    standard_status: str
    category: str | None
    note: str | None
    source_record_id: str | None


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def derive_person_status(source: Any, archived: Any) -> str:
    if isinstance(source, str) and source.strip():
        return STUDENT_STATUS.get(source.strip().lower(), "unknown")
    if archived is True:
        return "archived"
    if archived is False:
        return "active"
    return "unspecified"


def standardize_attendance(source: Any) -> str:
    if not isinstance(source, str) or not source.strip():
        return "unknown"
    return ATTENDANCE_STATUS.get(source.strip().lower(), "unknown")


def category_name(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = value.get("name") or value.get("title") or value.get("status")
        return str(name) if name else None
    return str(value)


def string_id(value: Any) -> str:
    if value is None or value == "":
        raise ShapeError("missing source id")
    return str(value)


def present_string(payload: dict, key: str, current: str | None) -> str | None:
    """Keep the stored value when ManageBac omits a null field."""
    if key not in payload:
        return current
    value = payload[key]
    if value is None:
        return None
    return str(value)


def present_bool(payload: dict, key: str, current: bool | None) -> bool | None:
    if key not in payload:
        return current
    value = payload[key]
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ShapeError(f"{key} must be a boolean or null")


def present_int(payload: dict, key: str, current: int | None) -> int | None:
    if key not in payload:
        return current
    value = payload[key]
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ShapeError(f"{key} must be an integer") from exc


def iter_academic_years(payload: dict) -> list[dict]:
    grouped = payload.get("academic_years")
    if not isinstance(grouped, dict):
        raise ShapeError("academic_years must be an object keyed by program")
    rows: list[dict] = []
    for program_code, block in grouped.items():
        years = block.get("academic_years") if isinstance(block, dict) else block
        if not isinstance(years, list):
            raise ShapeError(f"academic years for {program_code} must be a list")
        for year in years:
            if not isinstance(year, dict) or "id" not in year:
                raise ShapeError("academic year missing id")
            terms = year.get("academic_terms") or []
            if not isinstance(terms, list):
                raise ShapeError("academic_terms must be a list")
            rows.append({"program_code": str(program_code), "year": year, "terms": terms})
    return rows


def iter_subjects(payload: dict, program_code: str | None = None) -> list[dict]:
    subjects = payload.get("subjects")
    rows: list[dict] = []
    if isinstance(subjects, list):
        for subject in subjects:
            if not isinstance(subject, dict) or "id" not in subject:
                raise ShapeError("subject missing id")
            code = str(subject.get("program_code") or program_code or "")
            if not code:
                raise ShapeError("subject missing program_code")
            rows.append({"program_code": code, "subject": subject})
        return rows
    if isinstance(subjects, dict):
        for code, block in subjects.items():
            items = block.get("subjects") if isinstance(block, dict) else block
            if not isinstance(items, list):
                raise ShapeError("grouped subjects must be a list")
            for subject in items:
                if not isinstance(subject, dict) or "id" not in subject:
                    raise ShapeError("subject missing id")
                rows.append({"program_code": str(code), "subject": subject})
        return rows
    raise ShapeError("subjects collection has an unexpected shape")


def teacher_refs_from_class(payload: dict) -> list[dict] | None:
    """None means the payload did not say who teaches the class."""
    if "teachers" not in payload and "teacher_ids" not in payload:
        return None
    refs: list[dict] = []
    if "teacher_ids" in payload:
        raw_ids = payload.get("teacher_ids") or []
        if not isinstance(raw_ids, list):
            raise ShapeError("teacher_ids must be a list")
        for item in raw_ids:
            refs.append({"teacher_mb_id": string_id(item), "show_on_reports": None})
    teachers = payload.get("teachers")
    if teachers is None and "teachers" in payload:
        return refs
    if teachers is None:
        return refs
    if not isinstance(teachers, list):
        raise ShapeError("teachers must be a list")
    for teacher in teachers:
        if isinstance(teacher, dict):
            teacher_id = teacher.get("id", teacher.get("teacher_id"))
            show = teacher.get("show_on_reports")
            if show is not None and not isinstance(show, bool):
                raise ShapeError("show_on_reports must be a boolean")
            refs.append({"teacher_mb_id": string_id(teacher_id), "show_on_reports": show})
        else:
            refs.append({"teacher_mb_id": string_id(teacher), "show_on_reports": None})
    return refs


def student_ids_from_membership(payload: dict) -> list[tuple[str, str | None]]:
    levels: dict[str, str | None] = {}
    found = False
    if "student_ids" in payload:
        found = True
        raw_ids = payload.get("student_ids") or []
        if not isinstance(raw_ids, list):
            raise ShapeError("student_ids must be a list")
        for item in raw_ids:
            levels[string_id(item)] = None
    students = payload.get("students")
    if isinstance(students, list):
        found = True
        for student in students:
            if isinstance(student, dict):
                sid = string_id(student.get("id", student.get("student_id")))
                level = student.get("level")
                levels[sid] = str(level) if level else levels.get(sid)
            else:
                levels[string_id(student)] = levels.get(string_id(student))
    mapping = payload.get("memberships") or payload.get("levels")
    if isinstance(mapping, list):
        found = True
        for item in mapping:
            if not isinstance(item, dict):
                continue
            sid = string_id(item.get("student_id", item.get("id")))
            level = item.get("level")
            levels[sid] = str(level) if level else levels.get(sid)
    elif isinstance(mapping, dict):
        found = True
        for sid, level in mapping.items():
            levels[str(sid)] = str(level) if level else levels.get(str(sid))
    if not found:
        raise ShapeError("membership response did not include students")
    return [(sid, level) for sid, level in levels.items()]


def parse_class_attendance(payload: dict, *, class_mb_id: str, business_date: date) -> list[AttendanceDraft]:
    if "students" not in payload:
        raise ShapeError("class attendance response missing students")
    students = payload.get("students")
    if students is None:
        students = []
    if not isinstance(students, list):
        raise ShapeError("students must be a list")
    drafts: list[AttendanceDraft] = []
    for student in students:
        if not isinstance(student, dict) or "id" not in student:
            raise ShapeError("attendance student missing id")
        attendance = student.get("attendance", {})
        if attendance in (None, {}):
            continue
        if not isinstance(attendance, dict):
            raise ShapeError("attendance must be an object keyed by period")
        for period, info in attendance.items():
            if not isinstance(info, dict):
                raise ShapeError("period attendance must be an object")
            source = info.get("status") if "status" in info else None
            drafts.append(
                AttendanceDraft(
                    attendance_type="class",
                    student_mb_id=string_id(student.get("id")),
                    business_date=business_date,
                    class_mb_id=class_mb_id,
                    year_group_mb_id=None,
                    period=str(period),
                    source_status=str(source) if isinstance(source, str) else None,
                    standard_status=standardize_attendance(source),
                    category=category_name(info.get("category")),
                    note=info.get("note") if isinstance(info.get("note"), str) else None,
                    source_record_id=str(info["id"]) if info.get("id") is not None else None,
                )
            )
    return drafts


def parse_homeroom_attendance(payload: dict, *, year_group_mb_id: str, business_date: date) -> list[AttendanceDraft]:
    if "students" not in payload:
        raise ShapeError("homeroom attendance response missing students")
    students = payload.get("students") or []
    if not isinstance(students, list):
        raise ShapeError("students must be a list")
    drafts: list[AttendanceDraft] = []
    for student in students:
        if not isinstance(student, dict) or "id" not in student:
            raise ShapeError("homeroom student missing id")
        source = student.get("status") if "status" in student else None
        drafts.append(
            AttendanceDraft(
                attendance_type="homeroom",
                student_mb_id=string_id(student.get("id")),
                business_date=business_date,
                class_mb_id=None,
                year_group_mb_id=year_group_mb_id,
                period="homeroom",
                source_status=str(source) if isinstance(source, str) else None,
                standard_status=standardize_attendance(source),
                category=category_name(student.get("category")),
                note=student.get("note") if isinstance(student.get("note"), str) else None,
                source_record_id=str(student["attendance_id"]) if student.get("attendance_id") is not None else None,
            )
        )
    return drafts


def advisor_rows(payload: dict) -> list[tuple[str, str]]:
    """Return (teacher_mb_id, role). Unrecognized shapes raise so callers can record a quality issue."""
    if "advisors" not in payload and not any(key in payload for key in ("homeroom", "cas", "ee", "tok")):
        rows: list[tuple[str, str]] = []
        for key, value in payload.items():
            if isinstance(value, list):
                for item in value:
                    teacher_id = item.get("id") if isinstance(item, dict) else item
                    role = item.get("role") if isinstance(item, dict) else key
                    if teacher_id is not None:
                        rows.append((string_id(teacher_id), str(role or key)))
            elif isinstance(value, dict) and "id" in value:
                rows.append((string_id(value["id"]), str(value.get("role") or key)))
        return rows
    advisors = payload.get("advisors")
    rows = []
    if isinstance(advisors, list):
        for item in advisors:
            if not isinstance(item, dict):
                raise ShapeError("advisor entry must be an object")
            rows.append((string_id(item.get("id", item.get("teacher_id"))), str(item.get("role") or "advisor")))
        return rows
    if isinstance(advisors, dict):
        for role, items in advisors.items():
            if not isinstance(items, list):
                raise ShapeError("advisor role must list teachers")
            for item in items:
                teacher_id = item.get("id") if isinstance(item, dict) else item
                rows.append((string_id(teacher_id), str(role)))
        return rows
    raise ShapeError("advisors payload has an unexpected shape")
