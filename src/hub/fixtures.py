"""Recorded-shape responses for local development. These are not a school extract."""

from datetime import date


DEMO_ATTENDANCE_DATE = "2026-11-10"


def _meta(count: int, page: int = 1) -> dict:
    return {
        "current_page": page,
        "total_pages": 1,
        "total_count": count,
        "per_page": 100,
        "next_page": None,
        "prev_page": None,
    }


def resolve_fixture(path: str, params: dict) -> dict:
    page = int(params.get("page") or 1)
    if path.endswith("/auth/permissions"):
        return {"permissions": ["students", "teachers", "classes", "year_groups", "attendance", "behavior"]}
    if path.endswith("/school/academic-years"):
        return {
            "academic_years": {
                "diploma": {
                    "academic_years": [
                        {
                            "id": 100,
                            "name": "August 2026 – June 2027",
                            "active": True,
                            "starts_on": "2026-08-15",
                            "ends_on": "2027-06-20",
                            "updated_at": "2026-08-01T02:00:00+08:00",
                            "academic_terms": [
                                {
                                    "id": 201,
                                    "name": "Term 1",
                                    "active": True,
                                    "starts_on": "2026-08-15",
                                    "ends_on": "2026-12-18",
                                    "updated_at": "2026-08-01T02:00:00+08:00",
                                    "exam_grade": False,
                                },
                                {
                                    "id": 202,
                                    "name": "Term 2",
                                    "active": False,
                                    "starts_on": "2027-01-06",
                                    "ends_on": "2027-06-20",
                                    "updated_at": "2026-08-01T02:00:00+08:00",
                                    "exam_grade": False,
                                },
                            ],
                        }
                    ]
                }
            }
        }
    if path.endswith("/school/subjects"):
        return {
            "subjects": {
                "diploma": [
                    {"id": 301, "name": "English", "code": "ENG", "hl": True, "sl": True},
                    {"id": 302, "name": "Mathematics", "code": "MAT", "hl": False, "sl": False},
                ]
            }
        }
    if path.endswith("/teachers"):
        teachers = [
            {"id": 11, "first_name": "Ann", "last_name": "Lee", "email": "ann.lee@example.edu", "identifier": "T011"},
            {"id": 12, "first_name": "Ben", "last_name": "Ng", "email": "ben.ng@example.edu", "identifier": "T012"},
            {"id": 99, "first_name": "Kim", "last_name": "Advisor", "email": "kim.advisor@example.edu", "identifier": "T099"},
        ]
        if params.get("deleted_since"):
            teachers = []
        return {"teachers": teachers if page == 1 else [], "meta": _meta(len(teachers), page)}
    if path.endswith("/students") and "/classes/" not in path and "/year-groups/" not in path:
        students = [
            {
                "id": 1,
                "student_id": "S001",
                "first_name": "Ada",
                "last_name": "Chan",
                "email": "ada.chan@example.edu",
                "status": "enrolled",
                "year_group_id": 501,
                "program": "Diploma",
                "program_code": "diploma",
                "class_grade": "Grade 10",
                "updated_at": "2026-09-01T08:00:00+08:00",
            },
            {
                "id": 2,
                "student_id": "S002",
                "first_name": "Bob",
                "last_name": "Wu",
                "email": "bob.wu@example.edu",
                "archived": True,
                "withdrawn_on": "2025-02-18",
                "year_group_id": 501,
                "program": "Diploma",
                "program_code": "diploma",
                "class_grade": "Grade 10",
            },
            {
                "id": 3,
                "student_id": "S003",
                "first_name": "Cara",
                "last_name": "Ng",
                "email": "cara.ng@example.edu",
                "archived": False,
                "graduated_on": "2026-07-15",
                "year_group_id": 501,
                "program": "Diploma",
                "program_code": "diploma",
                "class_grade": "Grade 10",
            },
        ]
        if params.get("deleted_since"):
            students = []
        return {"students": students if page == 1 else [], "meta": _meta(len(students), page)}
    if path.endswith("/year-groups") and "/students" not in path and "/advisors" not in path and "/homeroom/" not in path:
        groups = [
            {
                "id": 501,
                "name": "Grade 10",
                "program": "Diploma",
                "program_code": "diploma",
                "grade": "Grade 10",
                "student_ids": [1, 2, 3],
                "archived": False,
            }
        ]
        return {"year_groups": groups if page == 1 else [], "meta": _meta(len(groups), page)}
    if path.endswith("/year-groups/501/students"):
        students = [{"id": 1}, {"id": 2}, {"id": 3}]
        return {"students": students if page == 1 else [], "meta": _meta(len(students), page)}
    if path.endswith("/year-groups/501/advisors"):
        return {
            "advisors": [
                {"id": 99, "role": "homeroom"},
                {"id": 11, "role": "cas"},
                {"id": 98, "roles": ["EE", "TOK"]},
            ]
        }
    if path.endswith("/classes") and "/students" not in path and "/attendance/" not in path:
        classes = [
            {
                "id": 801,
                "name": "English A",
                "uniq_id": "ENG10A",
                "program": "Diploma",
                "program_code": "diploma",
                "grade": "Grade 10",
                "subject_id": 301,
                "subject_name": "English",
                "start_term_id": 201,
                "end_term_id": 202,
                "teachers": [{"id": 11, "show_on_reports": True}],
            },
            {
                "id": 802,
                "name": "Mathematics A",
                "uniq_id": "MAT10A",
                "program": "Diploma",
                "program_code": "diploma",
                "grade": "Grade 10",
                "subject_id": 302,
                "subject_name": "Mathematics",
                "start_term_id": 201,
                "end_term_id": 202,
                "teachers": [{"id": 12, "show_on_reports": True}],
            },
        ]
        return {"classes": classes if page == 1 else [], "meta": _meta(len(classes), page)}
    if path.endswith("/classes/801/students"):
        return {"student_ids": [1, 2], "memberships": [{"student_id": 1, "level": "SL"}, {"student_id": 2, "level": "HL"}]}
    if path.endswith("/classes/802/students"):
        return {"student_ids": [1, 3], "student_levels": {"1": "SL", "3": "HL"}}
    if "/attendance/date/" in path and "/classes/" in path:
        day = path.rsplit("/", 1)[-1]
        if day != DEMO_ATTENDANCE_DATE:
            return {"students": []}
        if path.split("/classes/")[1].startswith("801/"):
            return {
                "students": [
                    {
                        "id": 1,
                        "attendance": {
                            "1": {"status": "Present", "category": "In class", "note": "on time"},
                            "2": {"status": "Late", "category": "In class", "note": "bus"},
                        },
                    },
                    {"id": 2, "attendance": {"1": {"status": "Absent", "category": "Unexcused"}}},
                ]
            }
        return {
            "students": [
                {"id": 1, "attendance": {"1": {"status": "Present", "category": "In class"}}},
                {"id": 3, "attendance": {"1": {"status": "Left-early", "category": "Other", "note": "clinic"}}},
            ]
        }
    if path.endswith("/behavior/notes"):
        notes = [
            {
                "id": 9001,
                "student_id": "S001",
                "email": "ada.chan@example.edu",
                "first_name": "Ada",
                "last_name": "Chan",
                "grade": "Grade 10",
                "incident_time": "2026-11-10T09:15:00+08:00",
                "behavior_type": "Positive",
                "notes": "Helped a classmate during lab.",
                "next_step": "Share with advisor",
                "next_step_date": "2026-11-12",
                "reported_by": "Ann Lee",
                "homeroom_advisor": "Kim Advisor",
                "author_id": 11,
                "visible_to_parents": True,
                "visible_to_students": False,
                "created_at": "2026-11-10T09:20:00+08:00",
                "updated_at": "2026-11-10T09:20:00+08:00",
            },
            {
                "id": 9002,
                "student_id": "1",
                "email": "ada.chan@example.edu",
                "first_name": "Ada",
                "last_name": "Chan",
                "grade": "Grade 10",
                "incident_time": "2026-11-11T14:00:00+08:00",
                "behavior_type": "Late to class",
                "notes": "Arrived after the second bell.",
                "next_step": None,
                "next_step_date": None,
                "reported_by": "Ben Ng",
                "homeroom_advisor": "Kim Advisor",
                "author_id": 12,
                "visible_to_parents": False,
                "visible_to_students": False,
                "created_at": "2026-11-11T14:05:00+08:00",
                "updated_at": "2026-11-11T14:05:00+08:00",
            },
        ]
        return {"behavior_notes": notes if page == 1 else [], "meta": _meta(len(notes), page)}
    if "/homeroom/attendance/date/" in path:
        day = path.rsplit("/", 1)[-1]
        if day != DEMO_ATTENDANCE_DATE:
            return {"students": []}
        return {
            "students": [
                {"id": 1, "attendance": {"status": "Present", "note": "", "category": {"status": 1}}},
                {"id": 2, "attendance": {"status": "Early Leave", "note": "left", "category": {"status": 1}}},
                {"id": 3, "attendance": {"status": "Sick Leave(Excused)", "note": "", "category": {"status": 2}}},
            ]
        }
    raise KeyError(path)


def demo_attendance_date() -> date:
    return date.fromisoformat(DEMO_ATTENDANCE_DATE)
