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
