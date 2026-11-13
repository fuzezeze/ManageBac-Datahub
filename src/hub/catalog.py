from dataclasses import dataclass


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    priority: int
    depends_on: tuple[str, ...] = ()
    blocked: bool = False
    block_reason: str = ""
    supports_dates: bool = False
    cadence: str = "daily"
    hour: int | None = 2
    minute: int = 0
    interval_minutes: int | None = None
    scheduled: bool = True


COURSE_BLOCK_REASON = (
    "ManageBac public API v2 exposes Subject and Class, not an independent Course resource. "
    "Courses are not synthesized from classes. Confirm an endpoint with ManageBac before enabling this dataset."
)


DATASETS: dict[str, DatasetSpec] = {
    spec.name: spec
    for spec in (
        DatasetSpec("connection_check", 0, cadence="interval", interval_minutes=15, hour=None, minute=0),
        DatasetSpec("academic_years", 10),
        DatasetSpec("subjects", 20, depends_on=("academic_years",)),
        DatasetSpec("teachers", 30),
        DatasetSpec("students", 30),
        DatasetSpec("year_groups", 40),
        DatasetSpec("classes", 40),
        DatasetSpec("student_year_groups", 50, depends_on=("year_groups", "students")),
        DatasetSpec("student_classes", 50, depends_on=("classes", "students")),
        DatasetSpec("teacher_classes", 50, depends_on=("classes", "teachers")),
        DatasetSpec("attendance_class", 60, depends_on=("classes",), supports_dates=True, cadence="hourly", hour=None, minute=15),
        DatasetSpec(
            "attendance_homeroom",
            60,
            depends_on=("year_groups",),
            supports_dates=True,
            cadence="hourly",
            hour=None,
            minute=15,
        ),
        DatasetSpec("behavior_notes", 55, depends_on=("students",)),
        DatasetSpec("courses", 70, blocked=True, block_reason=COURSE_BLOCK_REASON, scheduled=False),
    )
}

SCOPES = (
    "students:read",
    "students:contact",
    "teachers:read",
    "teachers:contact",
    "academics:read",
    "classes:read",
    "enrollments:read",
    "teaching-assignments:read",
    "year-groups:read",
    "attendance:read",
    "behavior:read",
)

@dataclass(frozen=True)
class ReadApiSpec:
    name: str
    title: str
    route: str
    scope: str
    fields: tuple[str, ...]


READ_APIS: dict[str, ReadApiSpec] = {
    spec.name: spec
    for spec in (
        ReadApiSpec(
            "students",
            "Students",
            "/api/v1/students",
            "students:read",
            ("id", "mb_id", "student_id", "first_name", "last_name", "other_name", "status", "source_status", "program", "grade", "year_group_id", "email"),
        ),
        ReadApiSpec(
            "teachers",
            "Teachers",
            "/api/v1/teachers",
            "teachers:read",
            ("id", "mb_id", "identifier", "first_name", "last_name", "other_name", "status", "source_status", "email"),
        ),
        ReadApiSpec(
            "academic_years",
            "Academic years",
            "/api/v1/academic-years",
            "academics:read",
            ("id", "mb_id", "program_code", "name", "starts_on", "ends_on", "active"),
        ),
        ReadApiSpec(
            "terms",
            "Terms",
            "/api/v1/terms",
            "academics:read",
            ("id", "mb_id", "academic_year_id", "program_code", "name", "starts_on", "ends_on", "active", "exam_grade"),
        ),
        ReadApiSpec(
            "subjects",
            "Subjects",
            "/api/v1/subjects",
            "academics:read",
            ("id", "mb_id", "program_code", "name", "code"),
        ),
        ReadApiSpec(
            "classes",
            "Classes",
            "/api/v1/classes",
            "classes:read",
            ("id", "mb_id", "name", "uniq_id", "program", "grade", "subject_id", "subject_name", "start_term_id", "end_term_id", "archived"),
        ),
        ReadApiSpec(
            "enrollments",
            "Class enrollments",
            "/api/v1/classes/{class_id}/students",
            "enrollments:read",
            ("class_id", "class_mb_id", "student_mb_id", "level", "member_status"),
        ),
        ReadApiSpec(
            "teaching_assignments",
            "Teaching assignments",
            "/api/v1/classes/{class_id}/teachers",
            "teaching-assignments:read",
            ("class_id", "class_mb_id", "teacher_mb_id", "role", "show_on_reports", "member_status"),
        ),
        ReadApiSpec(
            "year_groups",
            "Year groups",
            "/api/v1/year-groups",
            "year-groups:read",
            ("id", "mb_id", "name", "program", "program_code", "grade", "archived"),
        ),
        ReadApiSpec(
            "year_group_memberships",
            "Year group memberships",
            "/api/v1/year-groups/{year_group_id}/students",
            "year-groups:read",
            ("year_group_id", "year_group_mb_id", "student_mb_id", "member_status"),
        ),
        ReadApiSpec(
            "attendance",
            "Attendance",
            "/api/v1/attendance",
            "attendance:read",
            ("id", "attendance_type", "student_mb_id", "business_date", "class_mb_id", "year_group_mb_id", "period", "source_status", "status", "category", "note"),
        ),
        ReadApiSpec(
            "behavior_notes",
            "Behavior notes",
            "/api/v1/behavior-notes",
            "behavior:read",
            (
                "id",
                "mb_id",
                "student_mb_id",
                "student_id",
                "first_name",
                "last_name",
                "grade",
                "incident_time",
                "behavior_type",
                "notes",
                "next_step",
                "next_step_date",
                "reported_by",
                "homeroom_advisor",
                "author_id",
                "visible_to_parents",
                "visible_to_students",
                "email",
            ),
        ),
    )
}


SCOPE_DATASETS = {
    "students:read": ("students",),
    "students:contact": ("students",),
    "teachers:read": ("teachers",),
    "teachers:contact": ("teachers",),
    "academics:read": ("academic_years", "subjects"),
    "classes:read": ("classes",),
    "enrollments:read": ("student_classes",),
    "teaching-assignments:read": ("teacher_classes",),
    "year-groups:read": ("year_groups", "student_year_groups"),
    "attendance:read": ("attendance_class", "attendance_homeroom"),
    "behavior:read": ("behavior_notes",),
}
